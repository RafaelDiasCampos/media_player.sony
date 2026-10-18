"""Exercise HA discovery and real Sony HTTP across address changes."""

from unittest.mock import patch
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    SOURCE_DHCP,
    SOURCE_IGNORE,
    SOURCE_SSDP,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import requests
import requests_mock as requests_mock_lib

from custom_components.sony.api import (
    SonyClient,
    SonyConnectionError,
    SonyDeviceMismatch,
)
from custom_components.sony.const import (
    CONF_COOKIES,
    CONF_DMR_URL,
    CONF_ENTITY_ID_HOST,
    CONF_IRCC_URL,
    CONF_UDN,
    DOMAIN,
    SSDP_IRCC,
)
from custom_components.sony.discovery import updated_address

from .conftest import ENTRY_DATA, HOST, MAC
from .test_api import ACTION_LIST, COMMAND_LIST, DMR, IRCC, SOAP

NEW_HOST = "192.0.2.11"
UDN = "uuid:sony-bdv-e2100"


def ssdp_info(host=HOST, *, st=SSDP_IRCC, location=None):
    """Sony's IRCC and DMR descriptions can use different paths and ports."""
    return SsdpServiceInfo(
        ssdp_usn=f"{UDN}::{st}",
        ssdp_udn=UDN,
        ssdp_st=st,
        ssdp_location=location
        or (
            f"http://{host}:50001/Ircc.xml"
            if st == SSDP_IRCC
            else f"http://{host}:52323/dmr.xml"
        ),
        upnp={"UDN": UDN, "manufacturer": "Sony", "friendlyName": "Sony BDV-E2100"},
    )


@pytest.fixture
def moving_sony(requests_mock):
    """An authenticated Sony moves IP but consumes its pairing PIN only once."""
    peer = {"host": HOST, "mac": MAC, "registered": False, "registrations": 0}

    def response(request, context):
        parsed = urlsplit(request.url)
        if parsed.hostname != peer["host"]:
            raise requests.ConnectionError("Old address is offline")
        path = parsed.path
        if path in ("/dmr.xml", "/description.xml"):
            return DMR.replace("<device>", f"<device><UDN>{UDN}</UDN>")
        if path == "/Ircc.xml":
            return IRCC.replace(HOST, peer["host"])
        if path == "/actionList":
            return ACTION_LIST.replace(HOST, peer["host"])
        if path == "/systemInfo":
            if not peer["mac"]:
                return "<system />"
            return (
                '<system><supportFunction><function name="WOL">'
                f'<functionItem value="{peer["mac"]}" />'
                "</function></supportFunction></system>"
            )
        if path == "/commands":
            if "auth=move-session" not in request.headers.get("Cookie", ""):
                context.status_code = 403
                return "Forbidden"
            return COMMAND_LIST
        if path == "/register":
            peer["registrations"] += 1
            if (
                peer["registered"]
                or request.headers.get("Authorization") != "Basic OjAxMjM="
            ):
                context.status_code = 401
                return "Unauthorized"
            peer["registered"] = True
            context.headers["Set-Cookie"] = "auth=move-session; Path=/"
            return "<ok />"
        if path == "/upnp/control/AVTransport":
            return SOAP
        if path == "/upnp/control/IRCC":
            assert "auth=move-session" in request.headers.get("Cookie", "")
            return "<ok />"
        if path == "/appslist":
            return "<apps />"
        raise AssertionError(f"Unexpected Sony request: {request.url}")

    requests_mock.register_uri(
        requests_mock_lib.ANY, requests_mock_lib.ANY, text=response
    )

    async def cached_devices(hass, st):
        return [ssdp_info(peer["host"], st=st)]

    with patch(
        "homeassistant.components.ssdp.async_get_discovery_info_by_st",
        side_effect=cached_devices,
    ):
        yield peer


async def pair(hass, source=SOURCE_USER):
    """Pair using the real integration, API wrapper and one-time PIN handshake."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": source},
        data={"host": HOST} if source == SOURCE_USER else ssdp_info(),
    )
    if source == SOURCE_SSDP:
        assert result["step_id"] == "discovery_confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pin"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pin": "0123"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.LOADED
    return result["result"]


@pytest.mark.parametrize("source", [SOURCE_DHCP, SOURCE_SSDP, "ssdp_udn"])
async def test_ip_move_preserves_pairing_and_entities(hass, moving_sony, source):
    """A new IP produces usable commands with the same entry, entities and cookie."""
    if source == "ssdp_udn":
        moving_sony["mac"] = None
    entry = await pair(hass, SOURCE_SSDP if source == SOURCE_DHCP else SOURCE_USER)
    if source != "ssdp_udn":
        # Match the user's existing 2.1.1 entry, whose unique ID was the host.
        data = {
            k: v
            for k, v in entry.data.items()
            if k not in (CONF_UDN, CONF_ENTITY_ID_HOST)
        }
        hass.config_entries.async_update_entry(entry, unique_id=HOST, data=data)
    before = {
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    device_ids = {
        d.id
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    }
    registrations = moving_sony["registrations"]
    unique_id = entry.unique_id
    moving_sony["host"] = NEW_HOST
    data = (
        DhcpServiceInfo(ip=NEW_HOST, hostname="sony", macaddress="aabbccddeeff")
        if source == SOURCE_DHCP
        else ssdp_info(NEW_HOST)
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP if source == SOURCE_DHCP else SOURCE_SSDP},
        data=data,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["host"] == NEW_HOST
    assert entry.unique_id == unique_id
    assert {
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    } == before
    assert {
        d.id
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    } == device_ids
    remote_id = next(entity for entity in before if entity.startswith("remote."))
    await hass.services.async_call(
        "remote",
        "send_command",
        {"entity_id": remote_id, "command": "Up"},
        blocking=True,
    )
    assert moving_sony["registrations"] == registrations
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert not hass.config_entries.flow.async_progress()


async def test_ssdp_new_device_requires_confirmation(hass, moving_sony, requests_mock):
    """Discovery reads capabilities but cannot start registration before approval."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=ssdp_info()
    )
    assert result["step_id"] == "discovery_confirm"
    assert moving_sony["registrations"] == 0
    assert not any(
        "Authorization" in request.headers for request in requests_mock.request_history
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pin"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pin": "0123"}
    )
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.LOADED
    assert result["result"].unique_id == "mac:aa:bb:cc:dd:ee:ff"
    assert result["data"][CONF_DMR_URL].endswith(":52323/dmr.xml")
    assert result["data"][CONF_IRCC_URL].endswith(":50001/Ircc.xml")
    assert result["data"][CONF_UDN] == UDN


async def test_discovery_uses_nonstandard_description_url(
    hass, moving_sony, requests_mock
):
    """The advertised renderer path and port survive pairing and entry setup."""
    info = ssdp_info(
        st="urn:schemas-upnp-org:device:MediaRenderer:1",
        location=f"http://{HOST}:18080/description.xml",
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=info
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pin": "0123"}
    )
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.LOADED
    assert result["data"]["dmr_port"] == 18080
    assert result["data"][CONF_DMR_URL] == info.ssdp_location
    assert not any(
        "/dmr.xml" in request.url for request in requests_mock.request_history
    )


async def test_alternate_service_udn_does_not_reload_same_address(
    hass, client, config_entry
):
    """Separate IRCC/renderer UDNs must not cause a reload on each announcement."""
    client.udn = UDN
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client.udn = "uuid:sony-ircc-service"
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_SSDP}, data=ssdp_info()
        )
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_UDN] == UDN
    reload.assert_not_called()


async def test_unknown_dhcp_mac_is_ignored(hass, client):
    """DHCP announcements are used only for an already registered Sony MAC."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(
            ip=NEW_HOST, hostname="unknown", macaddress="001122334455"
        ),
    )
    assert result["reason"] == "not_configured"
    client.discover.assert_not_called()


async def test_manual_add_of_known_mac_updates_address(hass, client, config_entry):
    """A manually supplied new address can reconnect the same registered device."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"host": NEW_HOST}
    )
    assert result["reason"] == "already_configured"
    assert config_entry.data["host"] == NEW_HOST
    client.authenticate.assert_not_called()


@pytest.mark.parametrize(
    "mac", ["aabbccddeeff", "AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"]
)
async def test_dhcp_mac_formats_match(hass, client, config_entry, mac):
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, "mac_address": mac}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(ip=NEW_HOST, hostname="sony", macaddress="aabbccddeeff"),
    )
    assert result["reason"] == "already_configured"
    assert config_entry.data["host"] == NEW_HOST
    assert config_entry.data[CONF_ENTITY_ID_HOST] == HOST


async def test_different_mac_with_same_udn_does_not_move_entry(
    hass, client, config_entry
):
    """A generic/shared UDN cannot override a contradictory physical MAC."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, unique_id=UDN, data={**config_entry.data, CONF_UDN: UDN}
    )
    client.device.mac = "00:11:22:33:44:55"
    client.udn = UDN
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=ssdp_info(NEW_HOST)
    )
    assert result["step_id"] == "discovery_confirm"
    assert config_entry.data["host"] == HOST
    client.authenticate.assert_not_called()


async def test_ignored_ssdp_device_stays_ignored(hass, client):
    entry = MockConfigEntry(
        domain=DOMAIN, source=SOURCE_IGNORE, unique_id="mac:aa:bb:cc:dd:ee:ff", data={}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=ssdp_info()
    )
    assert result["reason"] == "already_configured"
    assert not entry.data
    client.authenticate.assert_not_called()


@pytest.mark.parametrize(
    "location",
    [
        "missing",
        "https://sony/dmr.xml",
        "http://u:p@sony/dmr.xml",
        "http://[::1]/dmr.xml",
    ],
)
async def test_invalid_ssdp_location(hass, client, location):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=ssdp_info(location=location)
    )
    assert result["reason"] == "invalid_discovery_info"
    client.discover.assert_not_called()


def test_different_device_does_not_receive_saved_cookie(moving_sony, requests_mock):
    moving_sony["mac"] = "00:11:22:33:44:55"
    data = {
        **ENTRY_DATA,
        CONF_COOKIES: [
            {"name": "auth", "value": "private-cookie", "domain": HOST, "path": "/"}
        ],
    }
    with pytest.raises(SonyDeviceMismatch):
        SonyClient(data).refresh()
    assert not any(
        "Cookie" in request.headers or "Authorization" in request.headers
        for request in requests_mock.request_history
    )
    assert moving_sony["registrations"] == 0


def test_address_update_preserves_cookie_scope_and_urls():
    data = {
        **ENTRY_DATA,
        CONF_DMR_URL: f"http://{HOST}:52323/custom/description.xml",
        CONF_IRCC_URL: f"http://{HOST}:50001/ircc.xml",
        CONF_COOKIES: [
            {
                "name": "auth",
                "value": "saved",
                "domain": HOST,
                "path": "/sony",
                "secure": False,
                "expires": 2000000000,
            }
        ],
    }
    moved = updated_address(data, NEW_HOST)
    assert moved[CONF_COOKIES][0] == {**data[CONF_COOKIES][0], "domain": NEW_HOST}
    assert moved[CONF_DMR_URL] == f"http://{NEW_HOST}:52323/custom/description.xml"
    assert moved[CONF_IRCC_URL] == f"http://{NEW_HOST}:50001/ircc.xml"
    assert moved[CONF_ENTITY_ID_HOST] == HOST
    assert data["host"] == HOST


async def test_discovery_connection_error_is_not_saved(hass, client):
    client.discover.side_effect = SonyConnectionError("Offline")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=ssdp_info()
    )
    assert result["reason"] == "cannot_connect"
    assert not hass.config_entries.async_entries(DOMAIN)
