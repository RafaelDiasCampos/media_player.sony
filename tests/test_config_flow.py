"""Exercise registration and reauthentication through Home Assistant."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
import pytest
from sonyapilib.device import AuthenticationResult
import voluptuous as vol

from custom_components.sony.api import SonyAuthenticationError, SonyConnectionError
from custom_components.sony.config_flow import STEP_USER_DATA_SCHEMA
from custom_components.sony.const import DOMAIN

from .conftest import HOST, MAC


async def test_pairing_preserves_pin_and_ports(hass, client):
    """Keep the same client across PIN steps and only save a successful PIN."""
    client.authenticate.side_effect = [
        AuthenticationResult.PIN_NEEDED,
        SonyAuthenticationError("Invalid PIN"),
        AuthenticationResult.SUCCESS,
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST, "dmr_port": 50202, "ircc_port": 52323}
    )
    assert result["step_id"] == "pin"
    assert not result["errors"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pin": "0001"}
    )
    assert result["step_id"] == "pin"
    assert result["errors"] == {"base": "invalid_auth"}
    assert not hass.config_entries.async_entries(DOMAIN)

    with patch("custom_components.sony.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": "0123"}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["pin"] == "0123"
    assert result["data"]["mac_address"] == MAC
    assert result["data"]["dmr_port"] == 50202
    assert result["data"]["ircc_port"] == 52323
    assert client.authenticate.call_args_list[-1].args == ("0123",)


async def test_no_pin_registration(hass, client):
    """Devices which do not request a PIN can complete the first step."""
    client.pin = None
    with patch("custom_components.sony.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={"host": HOST, "name": "Living room"},
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living room"
    assert result["data"]["pin"] is None


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (SonyAuthenticationError("Rejected"), "invalid_auth"),
        (SonyConnectionError("Offline"), "cannot_connect"),
        (RuntimeError("Library error"), "unknown"),
    ],
)
async def test_failed_registration_does_not_save(hass, client, error, message):
    """Failed authentication must never produce a config entry."""
    client.authenticate.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"host": HOST}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": message}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_duplicate_is_rejected_before_network(hass, client, config_entry):
    """Avoid registering a device which is already configured."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"host": f" {HOST} "}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    client.authenticate.assert_not_called()


@pytest.mark.parametrize(
    "host", ["http://192.0.2.10", "192.0.2.10:50001", "", "sony.local/path"]
)
async def test_invalid_host(hass, client, host):
    """Do not build device URLs from a URL or a host containing a port."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"host": host}
    )
    assert result["errors"] == {"host": "invalid_host"}
    client.authenticate.assert_not_called()


@pytest.mark.parametrize("port", [0, -1, 65536, "invalid"])
def test_invalid_port(port):
    """Reject ports which cannot be used by a TCP device."""
    with pytest.raises(vol.Invalid):
        STEP_USER_DATA_SCHEMA({"host": HOST, "dmr_port": port})


async def test_reauthentication_updates_existing_entry(hass, client, config_entry):
    """Repair the existing entry without changing its identity or making duplicates."""
    config_entry.add_to_hass(hass)
    client.authenticate.side_effect = [
        AuthenticationResult.PIN_NEEDED,
        AuthenticationResult.SUCCESS,
    ]
    client.pin = "0456"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": config_entry.entry_id,
            "unique_id": HOST,
        },
        data=dict(config_entry.data),
    )
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pin"
    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(return_value=True)
    ) as reload:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": "0456"}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["pin"] == "0456"
    assert config_entry.unique_id == HOST
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    reload.assert_awaited_once_with(config_entry.entry_id)
