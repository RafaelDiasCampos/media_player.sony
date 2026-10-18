"""Exercise real sonyapilib registration and wire requests with mock HTTP."""

import base64
import json
import struct
from unittest.mock import patch
from xml.etree import ElementTree

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
import pytest
import requests
from sonyapilib.device import AuthenticationResult

from custom_components.sony.api import (
    SonyAuthenticationError,
    SonyClient,
    SonyConnectionError,
)
from custom_components.sony.const import CONF_COOKIES, DOMAIN

from .conftest import ENTRY_DATA, HOST, MAC

DMR_URL = f"http://{HOST}:52323/dmr.xml"
IRCC_URL = f"http://{HOST}:50001/Ircc.xml"
ACTION_URL = f"http://{HOST}:50001/actionList"
CONTROL_URL = f"http://{HOST}:50001/upnp/control/IRCC"
REGISTER_URL = f"http://{HOST}:50001/register"
SYSTEM_URL = f"http://{HOST}/sony/system"
SOAP = (
    "<Envelope><Body><CurrentTransportState>PLAYING</CurrentTransportState>"
    "</Body></Envelope>"
)
DMR = """<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device><modelName>BDV-E2100</modelName><serviceList><service>
  <serviceId>urn:upnp-org:serviceId:AVTransport</serviceId>
  <controlURL>/upnp/control/AVTransport</controlURL>
  </service></serviceList></device></root>"""
IRCC = f"""<root xmlns="urn:schemas-upnp-org:device-1-0"
  xmlns:av="urn:schemas-sony-com:av">
  <device><av:X_UNR_DeviceInfo><av:X_CERS_ActionList_URL>{ACTION_URL}</av:X_CERS_ActionList_URL>
  </av:X_UNR_DeviceInfo><serviceList><service>
  <serviceId>urn:schemas-sony-com:serviceId:IRCC</serviceId>
  <controlURL>/upnp/control/IRCC</controlURL></service></serviceList>
  <av:X_IRCC_DeviceInfo><av:X_IRCC_CategoryList /></av:X_IRCC_DeviceInfo>
  </device></root>"""
ACTION_LIST = f'''<actionList>
  <action name="register" mode="3" url="{REGISTER_URL}" />
  <action name="getRemoteCommandList" url="http://{HOST}:50001/commands" />
  <action name="getSystemInformation" url="http://{HOST}:50001/systemInfo" />
  </actionList>'''
COMMAND_LIST = """<commands>
  <command name="Power" value="AAAAAQAAAAEAAAAvAw==" />
  <command name="Play" value="AAAAAQAAAAEAAAAaAw==" />
  <command name="Up" value="AAAAAQAAAAEAAAB0Aw==" />
  </commands>"""


@pytest.fixture
def legacy_http(requests_mock):
    """A Sony CERS v3 device which requests PIN 0123."""
    requests_mock.get(DMR_URL, text=DMR)
    requests_mock.get(IRCC_URL, text=IRCC)
    requests_mock.get(ACTION_URL, text=ACTION_LIST)
    requests_mock.get(f"http://{HOST}:50001/commands", text=COMMAND_LIST)
    requests_mock.get(
        f"http://{HOST}:50001/systemInfo",
        text=(
            '<system><supportFunction><function name="WOL">'
            f'<functionItem value="{MAC}" />'
            "</function></supportFunction></system>"
        ),
    )
    requests_mock.get(f"http://{HOST}:50202/appslist", text="<apps />")
    requests_mock.post(f"http://{HOST}:52323/upnp/control/AVTransport", text=SOAP)
    requests_mock.post(CONTROL_URL, text="<ok />")

    def register(request, context):
        expected = "Basic " + base64.b64encode(b":0123").decode()
        context.status_code = (
            200 if request.headers.get("Authorization") == expected else 401
        )
        return "<ok />"

    requests_mock.get(REGISTER_URL, text=register)
    return requests_mock


def test_real_pin_handshake_and_restart(legacy_http):
    """Discover URLs, request a PIN, reject a wrong PIN and restore on restart."""
    client = SonyClient(ENTRY_DATA)
    assert client.authenticate() == AuthenticationResult.PIN_NEEDED
    with pytest.raises(SonyAuthenticationError):
        client.authenticate("4321")
    assert client.authenticate("0123") == AuthenticationResult.SUCCESS
    assert client.pin == "0123"
    assert client.device.mac == MAC
    assert client.model == "BDV-E2100"
    assert client.refresh().state == MediaPlayerState.PLAYING
    # Startup verifies the existing registration without consuming the PIN again.
    restored = SonyClient({**ENTRY_DATA, "pin": client.pin})
    assert restored.refresh().state == MediaPlayerState.PLAYING
    assert restored.device.commands["Up"].value == "AAAAAQAAAAEAAAB0Aw=="


@pytest.fixture(params=[False, True], ids=["client-id", "cookie"])
def single_use_legacy_http(legacy_http, request):
    """A CERS device accepts a displayed PIN only once, then rejects /register."""
    registered = False
    require_cookie = request.param

    def commands(request, context):
        if not registered or (
            require_cookie
            and "auth=legacy-session" not in request.headers.get("Cookie", "")
        ):
            context.status_code = 403
            return "Forbidden"
        return COMMAND_LIST

    def register(request, context):
        nonlocal registered
        if registered or request.headers.get("Authorization") != "Basic OjAxMjM=":
            context.status_code = 401
            return "Unauthorized"
        registered = True
        if require_cookie:
            context.headers["Set-Cookie"] = "auth=legacy-session; Path=/"
        return "<ok />"

    legacy_http.get(f"http://{HOST}:50001/commands", text=commands)
    legacy_http.get(REGISTER_URL, text=register)
    return legacy_http


async def test_single_use_pin_survives_setup_and_reload(hass, single_use_legacy_http):
    """The real PIN flow must create entities and survive a new runtime client."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"host": HOST}
    )
    assert result["step_id"] == "pin"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pin": "0123"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    )
    assert (
        len([state for state in hass.states.async_all() if state.domain == "remote"])
        == 1
    )
    saved_data = json.loads(json.dumps(dict(entry.data)))
    registration_count = sum(
        item.url.startswith(REGISTER_URL)
        for item in single_use_legacy_http.request_history
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert (
        sum(
            item.url.startswith(REGISTER_URL)
            for item in single_use_legacy_http.request_history
        )
        == registration_count
    )

    # A completely new client can use only the JSON-serializable saved data.
    assert SonyClient(saved_data).refresh().state is MediaPlayerState.PLAYING
    assert not list(hass.config_entries.flow.async_progress())


def test_revoked_registration_never_replays_pin(single_use_legacy_http):
    """Revoked authorization must request reauth without silently registering."""
    client = SonyClient(ENTRY_DATA)
    assert client.authenticate("0123") is AuthenticationResult.SUCCESS
    saved_data = {**ENTRY_DATA, CONF_COOKIES: client.cookies}
    single_use_legacy_http.get(f"http://{HOST}:50001/commands", status_code=403)
    registration_count = sum(
        item.url.startswith(REGISTER_URL)
        for item in single_use_legacy_http.request_history
    )
    with pytest.raises(SonyAuthenticationError):
        SonyClient(saved_data).refresh()
    assert (
        sum(
            item.url.startswith(REGISTER_URL)
            for item in single_use_legacy_http.request_history
        )
        == registration_count
    )


def test_saved_basic_credential_does_not_register(legacy_http):
    """Devices with reusable Basic credentials can restore older config entries."""

    def commands(request, context):
        if request.headers.get("Authorization") != "Basic OjAxMjM=":
            context.status_code = 403
            return "Forbidden"
        return COMMAND_LIST

    legacy_http.get(f"http://{HOST}:50001/commands", text=commands)
    assert SonyClient(ENTRY_DATA).refresh().state is MediaPlayerState.PLAYING
    assert not any(
        item.url.startswith(REGISTER_URL) for item in legacy_http.request_history
    )


def test_ircc_keeps_authentication_headers(legacy_http):
    """SOAP commands must keep the CERS device ID, PIN header and SOAPAction."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    client.send_command("Up")
    request = legacy_http.last_request
    assert request.url == CONTROL_URL
    assert request.headers["X-CERS-DEVICE-ID"] == "Sony Device"
    assert request.headers["Authorization"] == "Basic OjAxMjM="
    assert (
        request.headers["SOAPACTION"]
        == '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"'
    )
    body = ElementTree.fromstring(request.text)
    assert body.findtext(".//IRCCCode") == "AAAAAQAAAAEAAAB0Aw=="


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, SonyAuthenticationError),
        (403, SonyAuthenticationError),
        (500, SonyConnectionError),
    ],
)
def test_ircc_http_errors(legacy_http, status, error):
    """Failed SOAP requests are never silently discarded."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    legacy_http.post(CONTROL_URL, status_code=status)
    with pytest.raises(error):
        client.send_command("Up")


def test_unknown_command_never_sends_http(legacy_http):
    """Only accept commands advertised by the device."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    count = legacy_http.call_count
    with pytest.raises(ValueError, match="Unsupported Sony command"):
        client.send_command("Unknown")
    assert legacy_http.call_count == count


@pytest.mark.parametrize("error", [requests.ConnectionError, requests.Timeout])
def test_connection_failure_is_not_power_off(legacy_http, error):
    """Do not confuse a failed network request with a confirmed standby state."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    legacy_http.get(ACTION_URL, exc=error)
    with pytest.raises(SonyConnectionError):
        client.refresh()


@pytest.mark.parametrize(
    ("playback", "state"),
    [
        ("PLAYING", MediaPlayerState.PLAYING),
        ("PAUSED_PLAYBACK", MediaPlayerState.PAUSED),
        ("STOPPED", MediaPlayerState.IDLE),
        ("NO_MEDIA_PRESENT", MediaPlayerState.ON),
    ],
)
def test_playback_states(legacy_http, playback, state):
    """Map transport status without making the remote report playback strings."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    legacy_http.post(
        f"http://{HOST}:52323/upnp/control/AVTransport",
        text=SOAP.replace("PLAYING", playback),
    )
    assert client.refresh().state == state


def test_optional_playback_status(legacy_http):
    """IRCC still works when AVTransport is not implemented for an input."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    legacy_http.post(f"http://{HOST}:52323/upnp/control/AVTransport", status_code=500)
    assert client.refresh().state == MediaPlayerState.ON
    client.send_command("Up")


@pytest.mark.parametrize(
    ("power", "state"),
    [
        ("active", MediaPlayerState.PLAYING),
        ("standby", MediaPlayerState.OFF),
        ("off", MediaPlayerState.OFF),
    ],
)
def test_scalar_power_status(legacy_http, power, state):
    """Scalar standby is off; merely having an HTTP response does not mean on."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    client.device.api_version = 4
    legacy_http.post(SYSTEM_URL, json={"result": [{"status": power}]})
    assert client.refresh().state == state


@pytest.mark.parametrize(
    "payload", [{"error": [403, "Forbidden"]}, {"error": [401, "Unauthorized"]}]
)
def test_scalar_json_authentication_error(legacy_http, payload):
    """Sony also reports rejected credentials in JSON with HTTP status 200."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    client.device.api_version = 4
    legacy_http.post(SYSTEM_URL, json=payload)
    with pytest.raises(SonyAuthenticationError):
        client.refresh()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"result": []},
        {"result": [{"status": "invalid"}]},
        {"error": [500, "Failed"]},
    ],
)
def test_scalar_invalid_power_response(legacy_http, payload):
    """Malformed status responses should make the entity unavailable."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    client.device.api_version = 4
    legacy_http.post(SYSTEM_URL, json=payload)
    with pytest.raises(SonyConnectionError):
        client.refresh()


def test_power_does_not_toggle_already_on_device(legacy_http):
    """Both turn_on and turn_off must be idempotent for toggle-only remotes."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    with patch.object(client.device, "wakeonlan") as wake:
        client.set_power(True)
        wake.assert_called_once()
    assert not any(
        request.url == CONTROL_URL for request in legacy_http.request_history
    )
    client.device.api_version = 4
    legacy_http.post(SYSTEM_URL, json={"result": [{"status": "standby"}]})
    client.set_power(False)
    assert not any(
        request.url == CONTROL_URL for request in legacy_http.request_history
    )


def test_failed_registration_does_not_authenticate(legacy_http):
    """An upstream registration error is not treated as successful setup."""
    legacy_http.get(REGISTER_URL, status_code=500)
    with pytest.raises(SonyConnectionError):
        SonyClient(ENTRY_DATA).authenticate()


@pytest.fixture
def scalar_http(requests_mock):
    """A Scalar v4 device using a registration cookie and JSON APIs."""
    description = DMR.replace(
        'xmlns="urn:schemas-upnp-org:device-1-0"',
        'xmlns="urn:schemas-upnp-org:device-1-0" xmlns:av="urn:schemas-sony-com:av"',
    ).replace(
        "</device>",
        "<av:X_ScalarWebAPI_DeviceInfo>"
        "<av:X_ScalarWebAPI_ServiceType>system</av:X_ScalarWebAPI_ServiceType>"
        f"<av:X_ScalarWebAPI_BaseURL>http://{HOST}/sony/</av:X_ScalarWebAPI_BaseURL>"
        "</av:X_ScalarWebAPI_DeviceInfo></device>",
    )
    requests_mock.get(DMR_URL, text=description)

    def system(request, context):
        method = request.json()["method"]
        if method == "getSystemSupportedFunction":
            return {"result": [[{"option": "WOL", "value": MAC}]]}
        if "auth=sony-test-cookie" not in request.headers.get("Cookie", ""):
            context.status_code = 403
            return {"error": [403, "Forbidden"]}
        if method == "getRemoteControllerInfo":
            return {
                "result": [
                    {},
                    [
                        {"name": "PowerOff", "value": "AAAAAQAAAAEAAAAvAw=="},
                        {"name": "Up", "value": "AAAAAQAAAAEAAAB0Aw=="},
                    ],
                ]
            }
        if method == "getPowerStatus":
            return {"result": [{"status": "active"}]}
        raise AssertionError(f"Unexpected Sony method: {method}")

    def register(request, context):
        if request.headers.get("Authorization") != "Basic OjAxMjM=":
            context.status_code = 401
            return {"error": [401, "Unauthorized"]}
        context.headers["Set-Cookie"] = "auth=sony-test-cookie; Path=/"
        return {"result": []}

    requests_mock.post(SYSTEM_URL, json=system)
    requests_mock.post(f"http://{HOST}/sony/accessControl", json=register)
    requests_mock.get(f"http://{HOST}/DIAL/sony/applist", text="<apps />")
    requests_mock.post(f"http://{HOST}:52323/upnp/control/AVTransport", text=SOAP)
    requests_mock.post(f"http://{HOST}/sony/IRCC", text="<ok />")
    return requests_mock


def test_scalar_pairing_cookie_and_restart(scalar_http):
    """Complete a real Scalar v4 handshake, then restore it from a saved PIN."""
    client = SonyClient(ENTRY_DATA)
    assert client.authenticate() == AuthenticationResult.PIN_NEEDED
    assert client.authenticate("0123") == AuthenticationResult.SUCCESS
    assert client.device.api_version == 4
    client.send_command("Up")
    assert "auth=sony-test-cookie" in scalar_http.last_request.headers["Cookie"]
    assert client.refresh().state == MediaPlayerState.PLAYING
    saved_data = {**ENTRY_DATA, CONF_COOKIES: client.cookies}
    scalar_http.post(f"http://{HOST}/sony/accessControl", status_code=401)
    restored = SonyClient(saved_data)
    assert restored.refresh().state == MediaPlayerState.PLAYING
    restored.send_command("Up")
    assert "auth=sony-test-cookie" in scalar_http.last_request.headers["Cookie"]


def test_cookie_scope_and_expiry_survive_restart(scalar_http):
    """Persist cookie path, domain, security and expiry instead of only its value."""
    client = SonyClient(ENTRY_DATA)
    client.device.cookies.set(
        "auth",
        "scoped-cookie",
        domain=HOST,
        path="/sony",
        secure=True,
        expires=2000000000,
    )
    assert (
        SonyClient({**ENTRY_DATA, CONF_COOKIES: client.cookies}).cookies
        == client.cookies
    )


def test_scalar_missing_cookie_requires_pairing(scalar_http):
    """An old PIN alone cannot replace a missing Scalar registration cookie."""
    with pytest.raises(SonyAuthenticationError):
        SonyClient(ENTRY_DATA).refresh()
    assert not any(
        item.url.endswith("/accessControl") for item in scalar_http.request_history
    )


def test_scalar_already_registered_without_pin(scalar_http):
    """A PIN-free registration still loads commands and keeps the auth cookie."""
    scalar_http.post(
        f"http://{HOST}/sony/accessControl",
        json={"result": []},
        headers={"Set-Cookie": "auth=sony-test-cookie; Path=/"},
    )
    client = SonyClient({"host": HOST})
    assert client.authenticate() == AuthenticationResult.SUCCESS
    assert client.pin is None
    assert "Up" in client.refresh().commands
    client.send_command("Up")
    assert "auth=sony-test-cookie" in scalar_http.last_request.headers["Cookie"]


def test_ircc_only_device_without_registration(legacy_http):
    """Version 0 devices use their advertised built-in IRCC category."""
    category = base64.b64encode(struct.pack(">HI", 2, 7258)).decode()
    legacy_http.get(
        IRCC_URL,
        text=IRCC.replace(
            "<av:X_IRCC_CategoryList />",
            "<av:X_IRCC_CategoryList><av:X_IRCC_Category>"
            f"<av:X_CategoryInfo>{category}</av:X_CategoryInfo>"
            "</av:X_IRCC_Category></av:X_IRCC_CategoryList>",
        ),
    )
    legacy_http.get(ACTION_URL, text="<actionList />")
    client = SonyClient(ENTRY_DATA)
    assert client.authenticate() == AuthenticationResult.SUCCESS
    assert client.device.api_version == 0
    assert "Play" in client.refresh().commands
    client.send_command("Up")


def test_legacy_registration_without_pin(legacy_http):
    """Normalize sonyapilib's numeric sentinel before saving entry data."""
    legacy_http.get(ACTION_URL, text=ACTION_LIST.replace('mode="3"', 'mode="1"'))
    legacy_http.get(REGISTER_URL, text="<ok />")
    client = SonyClient(ENTRY_DATA)
    assert client.authenticate() == AuthenticationResult.SUCCESS
    assert client.pin == "9999"
    assert (
        SonyClient({**ENTRY_DATA, "pin": client.pin}).refresh().state
        == MediaPlayerState.PLAYING
    )


def test_wake_on_lan_without_power_command(legacy_http):
    """MAC-only wake support must not try an unadvertised Power command."""
    client = SonyClient(ENTRY_DATA)
    client.authenticate("0123")
    client.device.api_version = 4
    del client.device.commands["Power"]
    legacy_http.post(SYSTEM_URL, json={"result": [{"status": "standby"}]})
    with patch.object(client.device, "wakeonlan") as wake:
        client.set_power(True)
        wake.assert_called_once()


def test_registration_requires_readable_commands(legacy_http):
    """Do not complete pairing with an unusable remote after a partial failure."""
    legacy_http.get(f"http://{HOST}:50001/commands", status_code=500)
    client = SonyClient(ENTRY_DATA)
    with pytest.raises(SonyConnectionError, match="command list"):
        client.authenticate("0123")
    legacy_http.get(f"http://{HOST}:50001/commands", text=COMMAND_LIST)
    assert client.authenticate("0123") == AuthenticationResult.SUCCESS
    assert "Up" in client.refresh().commands
