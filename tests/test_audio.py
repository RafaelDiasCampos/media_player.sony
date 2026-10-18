"""Exercise Sony audio discovery, captured SOAP formats and actual HA services."""

from xml.etree import ElementTree

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.exceptions import HomeAssistantError
import pytest
import requests

from custom_components.sony.api import SonyClient, SonyConnectionError

from .conftest import ENTRY_DATA, HOST
from .test_api import DMR, DMR_URL
from .test_api import legacy_http as legacy_http
from .test_integration import setup_entry

SERVICE = "urn:schemas-upnp-org:service:RenderingControl:1"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
ENCODING = "http://schemas.xmlsoap.org/soap/encoding/"
CONTROL = f"http://{HOST}:52323/upnp/control/RenderingControl"
SCPD_URL = f"http://{HOST}:52323/RenderingControlBdvSCPD.xml"
RENDERING_SERVICE = f"""<service>
  <serviceType>{SERVICE}</serviceType>
  <serviceId>urn:upnp-org:serviceId:RenderingControl</serviceId>
  <SCPDURL>/RenderingControlBdvSCPD.xml</SCPDURL>
  <controlURL>/upnp/control/RenderingControl</controlURL>
</service>"""
AUDIO_DMR = DMR.replace("<serviceList>", f"<serviceList>{RENDERING_SERVICE}")
# The BDV-E2100 advertises these actions and this 0..100 range. The real TV-input
# response supplied by the user was CurrentVolume=73, even with no disc loaded.
SCPD = """<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <actionList>
    <action><name>GetVolume</name></action>
    <action><name>SetVolume</name></action>
    <action><name>GetMute</name></action>
    <action><name>SetMute</name></action>
  </actionList>
  <serviceStateTable><stateVariable sendEvents="no">
    <name>Volume</name><dataType>ui2</dataType><allowedValueRange>
      <minimum>0</minimum><maximum>100</maximum><step>1</step>
    </allowedValueRange>
  </stateVariable></serviceStateTable>
</scpd>"""
FAULT = f"""<s:Envelope xmlns:s="{SOAP_NS}" s:encodingStyle="{ENCODING}">
  <s:Body><s:Fault><faultcode>s:Client</faultcode>
    <faultstring>UPnPError</faultstring><detail>
    <UPnPError xmlns="urn:schemas-upnp-org:control-1-0">
      <errorCode>501</errorCode><errorDescription>Action Failed</errorDescription>
    </UPnPError></detail></s:Fault></s:Body></s:Envelope>"""


@pytest.fixture
def audio_http(legacy_http):
    """A real CERS/IRCC client with independently controllable UPnP audio."""
    peer = {"volume": 73, "muted": False, "failures": {}, "set_calls": []}
    legacy_http.get(DMR_URL, text=AUDIO_DMR)
    legacy_http.get(SCPD_URL, text=SCPD)

    def audio(request, context):
        envelope = ElementTree.fromstring(request.text)
        # Missing encodingStyle reproduced HTTP 500/UPnP 501 on the real BDV.
        assert envelope.attrib[f"{{{SOAP_NS}}}encodingStyle"] == ENCODING
        body = envelope.find(f"{{{SOAP_NS}}}Body")
        assert len(body) == 1
        action = body[0].tag.removeprefix(f"{{{SERVICE}}}")
        assert request.headers["SOAPACTION"] == f'"{SERVICE}#{action}"'
        assert request.headers["X-CERS-DEVICE-ID"] == "Sony Device"
        assert body[0].findtext("InstanceID") == "0"
        assert body[0].findtext("Channel") == "Master"
        if failure := peer["failures"].get(action):
            if isinstance(failure, Exception):
                raise failure
            context.status_code, text = failure
            return text
        result = ""
        if action == "GetVolume":
            result = f"<CurrentVolume>{peer['volume']}</CurrentVolume>"
        elif action == "GetMute":
            mute = peer["muted"]
            value = int(mute) if isinstance(mute, bool) else mute
            result = f"<CurrentMute>{value}</CurrentMute>"
        elif action == "SetVolume":
            peer["volume"] = int(body[0].findtext("DesiredVolume"))
            peer["set_calls"].append((action, peer["volume"]))
        elif action == "SetMute":
            peer["muted"] = body[0].findtext("DesiredMute") == "1"
            peer["set_calls"].append((action, peer["muted"]))
        else:
            raise AssertionError(f"Unexpected audio action: {action}")
        return (
            '<?xml version="1.0"?>'
            f'<s:Envelope xmlns:s="{SOAP_NS}" s:encodingStyle="{ENCODING}">'
            f'<s:Body><u:{action}Response xmlns:u="{SERVICE}">{result}'
            f"</u:{action}Response></s:Body></s:Envelope>"
        )

    legacy_http.post(CONTROL, text=audio)
    peer["callback"] = audio
    return peer


@pytest.mark.parametrize(("volume", "muted"), [(73, False), (0, True), (100, False)])
def test_audio_readback(legacy_http, audio_http, volume, muted):
    """Normalize captured volume, including zero, and retain a false mute value."""
    audio_http.update(volume=volume, muted=muted)
    client = SonyClient(ENTRY_DATA)
    status = client.refresh()
    assert status.volume_level == volume / 100
    assert status.is_volume_muted is muted
    assert status.can_set_volume
    assert status.can_set_mute
    assert audio_http["set_calls"] == []


def test_audio_uses_advertised_urls_and_urlbase(legacy_http, audio_http):
    """Service descriptions may move to a different port and relative path."""
    base = f"http://{HOST}:60000/renderer/"
    description = (
        AUDIO_DMR.replace("<device>", f"<URLBase>{base}</URLBase><device>")
        .replace("/RenderingControlBdvSCPD.xml", "descriptions/audio.xml")
        .replace("/upnp/control/RenderingControl", "controls/audio")
    )
    legacy_http.get(DMR_URL, text=description)
    legacy_http.get(f"{base}descriptions/audio.xml", text=SCPD)
    legacy_http.post(f"{base}controls/audio", text=audio_http["callback"])
    assert SonyClient(ENTRY_DATA).refresh().volume_level == 0.73
    assert not any(
        request.url in (CONTROL, SCPD_URL) for request in legacy_http.request_history
    )


def test_audio_without_setters(legacy_http, audio_http):
    """Read-only devices must not expose a slider or mute setter."""
    legacy_http.get(
        SCPD_URL,
        text=SCPD.replace("<action><name>SetVolume</name></action>", "").replace(
            "<action><name>SetMute</name></action>", ""
        ),
    )
    client = SonyClient(ENTRY_DATA)
    status = client.refresh()
    assert status.volume_level == 0.73
    assert status.is_volume_muted is False
    assert not status.can_set_volume
    assert not status.can_set_mute
    count = legacy_http.call_count
    with pytest.raises(ValueError, match="Unsupported Sony audio action"):
        client.set_volume_level(0.4)
    with pytest.raises(ValueError, match="Unsupported Sony audio action"):
        client.mute_volume(True)
    assert legacy_http.call_count == count


@pytest.mark.parametrize(
    "range_xml",
    [
        "",
        "<minimum>0</minimum><maximum>0</maximum>",
        "<minimum>100</minimum><maximum>10</maximum>",
        "<minimum>0</minimum><maximum>100</maximum><step>0</step>",
        "<minimum>0</minimum><maximum>100</maximum><step>invalid</step>",
        "<minimum>invalid</minimum><maximum>100</maximum>",
    ],
)
def test_unknown_volume_range_keeps_mute(legacy_http, audio_http, range_xml):
    """Do not guess a 0..100 scale when the description is missing or invalid."""
    legacy_http.get(
        SCPD_URL,
        text=SCPD.replace(
            "<minimum>0</minimum><maximum>100</maximum><step>1</step>", range_xml
        ),
    )
    client = SonyClient(ENTRY_DATA)
    status = client.refresh()
    assert status.volume_level is None
    assert not status.can_set_volume
    assert status.is_volume_muted is False
    assert status.can_set_mute
    with pytest.raises(ValueError, match="usable volume range"):
        client.set_volume_level(0.4)


@pytest.mark.parametrize(
    ("action", "failure"),
    [
        ("GetVolume", (500, FAULT)),
        ("GetMute", (500, FAULT)),
        ("GetVolume", (200, FAULT)),
        ("GetMute", (200, "<broken")),
        ("GetVolume", (200, "<unrelated />")),
        ("GetMute", (403, "Forbidden")),
        ("GetVolume", requests.Timeout("Audio timed out")),
    ],
)
def test_optional_audio_failure_and_recovery(legacy_http, audio_http, action, failure):
    """An optional failed field becomes unknown while IRCC and the other field work."""
    audio_http["failures"][action] = failure
    client = SonyClient(ENTRY_DATA)
    status = client.refresh()
    assert status.state == "playing"
    assert status.volume_level == (None if action == "GetVolume" else 0.73)
    assert status.is_volume_muted is (None if action == "GetMute" else False)
    client.send_command("Up")
    audio_http["failures"].clear()
    status = client.refresh()
    assert status.volume_level == 0.73
    assert status.is_volume_muted is False


@pytest.mark.parametrize(
    ("key", "value"),
    [("volume", -1), ("volume", 101), ("volume", "invalid"), ("muted", "unknown")],
)
def test_invalid_audio_value_is_unknown(legacy_http, audio_http, key, value):
    """Never publish invented defaults or out-of-range device data."""
    audio_http[key] = value
    status = SonyClient(ENTRY_DATA).refresh()
    if key == "volume":
        assert status.volume_level is None
        assert not status.can_set_volume
    else:
        assert status.is_volume_muted is None
        assert not status.can_set_mute


@pytest.mark.parametrize(
    ("minimum", "maximum", "step", "level", "raw"),
    [
        (0, 100, 1, 0.73, 73),
        (0, 100, 1, 0, 0),
        (0, 100, 1, 1, 100),
        (10, 90, 5, 0.43, 45),
        (0, 100, 3, 1, 99),
    ],
)
def test_absolute_volume_uses_advertised_range(
    legacy_http, audio_http, minimum, maximum, step, level, raw
):
    """Honor the device's range and step instead of simulating button presses."""
    legacy_http.get(
        SCPD_URL,
        text=SCPD.replace(
            "<minimum>0</minimum><maximum>100</maximum><step>1</step>",
            f"<minimum>{minimum}</minimum><maximum>{maximum}</maximum><step>{step}</step>",
        ),
    )
    client = SonyClient(ENTRY_DATA)
    client.refresh()
    client.set_volume_level(level)
    assert audio_http["set_calls"] == [("SetVolume", raw)]
    assert client.refresh().volume_level == (raw - minimum) / (maximum - minimum)


@pytest.mark.parametrize("volume", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_volume_never_sends_command(legacy_http, audio_http, volume):
    """Reject non-finite and out-of-range service values before any HTTP request."""
    client = SonyClient(ENTRY_DATA)
    client.refresh()
    count = legacy_http.call_count
    with pytest.raises(ValueError, match="between 0 and 1"):
        client.set_volume_level(volume)
    assert legacy_http.call_count == count


@pytest.mark.parametrize("description", ["<broken", "<scpd />"])
def test_bad_audio_description_keeps_remote(legacy_http, audio_http, description):
    """An optional malformed or empty SCPD must not break pairing or commands."""
    legacy_http.get(SCPD_URL, text=description)
    client = SonyClient(ENTRY_DATA)
    assert client.refresh().volume_level is None
    client.send_command("Up")


def test_missing_audio_service_keeps_existing_controls(legacy_http):
    """Older devices continue working without any RenderingControl requests."""
    client = SonyClient(ENTRY_DATA)
    assert client.refresh().volume_level is None
    assert not any(request.url == CONTROL for request in legacy_http.request_history)
    with pytest.raises(ValueError, match="usable volume range"):
        client.set_volume_level(0.4)


async def test_ha_volume_and_mute_services(hass, config_entry, legacy_http, audio_http):
    """Real HA services send SOAP setters and immediately display fresh readings."""
    media_id, remote_id = await setup_entry(hass, config_entry)
    state = hass.states.get(media_id)
    assert state.attributes["volume_level"] == 0.73
    assert state.attributes["is_volume_muted"] is False
    assert state.attributes["supported_features"] & MediaPlayerEntityFeature.VOLUME_SET
    assert state.attributes["supported_features"] & MediaPlayerEntityFeature.VOLUME_MUTE
    assert "source" not in state.attributes
    assert "sound_mode" not in state.attributes
    await hass.services.async_call(
        "media_player",
        "volume_set",
        {"entity_id": media_id, "volume_level": 0.42},
        blocking=True,
    )
    assert hass.states.get(media_id).attributes["volume_level"] == 0.42
    for muted in (True, True, False):
        await hass.services.async_call(
            "media_player",
            "volume_mute",
            {"entity_id": media_id, "is_volume_muted": muted},
            blocking=True,
        )
        assert hass.states.get(media_id).attributes["is_volume_muted"] is muted
    assert audio_http["set_calls"] == [
        ("SetVolume", 42),
        ("SetMute", True),
        ("SetMute", True),
        ("SetMute", False),
    ]
    audio_http["failures"]["GetVolume"] = (500, FAULT)
    await config_entry.runtime_data.async_refresh()
    assert hass.states.get(media_id).attributes.get("volume_level") is None
    assert hass.states.get(media_id).state == "playing"
    assert hass.states.get(remote_id).state == "on"
    assert not hass.config_entries.flow.async_progress()


@pytest.mark.parametrize("failure", [(500, FAULT), (200, FAULT), (403, "Forbidden")])
async def test_failed_setter_reports_error_without_pin_reauth(
    hass, config_entry, legacy_http, audio_http, failure
):
    """A DLNA permission error must not invalidate the separate CERS registration."""
    media_id, _ = await setup_entry(hass, config_entry)
    audio_http["failures"]["SetVolume"] = failure
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "media_player",
            "volume_set",
            {"entity_id": media_id, "volume_level": 0.42},
            blocking=True,
        )
    assert not hass.config_entries.flow.async_progress()
    assert audio_http["volume"] == 73


def test_setter_rejects_invalid_soap_response(legacy_http, audio_http):
    """HTTP success alone must not turn a malformed setter response into success."""
    client = SonyClient(ENTRY_DATA)
    client.refresh()
    audio_http["failures"]["SetMute"] = (200, "<unrelated />")
    with pytest.raises(SonyConnectionError):
        client.mute_volume(True)
