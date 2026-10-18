"""Wake a sleeping Sony without HTTP, including after Home Assistant restarts."""

from unittest.mock import patch

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
import requests

from custom_components.sony.api import SonyAuthenticationError, SonyClient

from .conftest import ENTRY_DATA, MAC
from .test_api import DMR, DMR_URL
from .test_api import legacy_http as legacy_http
from .test_integration import setup_entry


def test_wol_uses_saved_mac_without_http(requests_mock):
    """A real magic packet can be constructed and sent before any discovery."""
    client = SonyClient(ENTRY_DATA)
    with patch("wakeonlan.create_socket") as create_socket:
        client.set_power(True)
    socket = create_socket.return_value.__enter__.return_value
    packet = b"\xff" * 6 + bytes.fromhex(MAC.replace(":", "")) * 16
    socket.send.assert_called_once_with(packet)
    assert create_socket.call_args.kwargs["host"] == "255.255.255.255"
    assert create_socket.call_args.kwargs["port"] == 9
    assert requests_mock.call_count == 0


@pytest.mark.parametrize(
    "domain,service",
    [
        ("media_player", "turn_on"),
        ("remote", "turn_on"),
        ("remote", "toggle"),
    ],
)
async def test_startup_while_asleep_then_wake(
    hass, config_entry, legacy_http, freezer, domain, service
):
    """HA can load a saved asleep device and accept a power-on service."""
    peer = {"awake": False}

    def description(request, context):
        if not peer["awake"]:
            raise requests.ConnectionError("Sony is asleep")
        return DMR

    legacy_http.get(DMR_URL, text=description)
    media_id, remote_id = await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(media_id).state == "unknown"
    assert hass.states.get(remote_id).state == "unknown"
    assert hass.states.get(media_id).attributes["supported_features"] == (
        MediaPlayerEntityFeature.TURN_ON
    )
    count = legacy_http.call_count
    with patch("wakeonlan.create_socket") as create_socket:
        await hass.services.async_call(
            domain,
            service,
            {"entity_id": remote_id if domain == "remote" else media_id},
            blocking=True,
        )
    create_socket.return_value.__enter__.return_value.send.assert_called_once()
    # Sending WOL is not followed by an immediate request to the sleeping server.
    assert legacy_http.call_count == count
    peer["awake"] = True
    freezer.tick(6)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(media_id).state == "playing"
    assert hass.states.get(remote_id).state == "on"
    assert not hass.config_entries.flow.async_progress()
    assert not any(
        "/register" in request.url for request in legacy_http.request_history
    )
    assert config_entry.runtime_data._wake_retries == 0


async def test_wake_retries_then_returns_to_normal_polling(
    hass, config_entry, client, freezer
):
    """A lost wake packet must not cause an endless fast poll or false on state."""
    media_id, _ = await setup_entry(hass, config_entry)
    client.refresh.side_effect = requests.ConnectionError("Asleep")
    await config_entry.runtime_data.async_refresh()
    assert hass.states.get(media_id).state == "unknown"
    client.refresh.reset_mock()
    await hass.services.async_call(
        "media_player", "turn_on", {"entity_id": media_id}, blocking=True
    )
    client.refresh.assert_not_called()
    for _ in range(6):
        freezer.tick(6)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
    assert client.refresh.call_count == 6
    assert config_entry.runtime_data._wake_retries == 0
    assert hass.states.get(media_id).state == "unknown"
    client.refresh.side_effect = None


async def test_unload_cancels_wake_checks(hass, config_entry, client, freezer):
    """Unloading an integration cancels its pending background wake timer."""
    media_id, _ = await setup_entry(hass, config_entry)
    await hass.services.async_call(
        "media_player", "turn_on", {"entity_id": media_id}, blocking=True
    )
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    count = client.refresh.call_count
    freezer.tick(40)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert client.refresh.call_count == count


async def test_wake_checks_stop_for_reauthentication(
    hass, config_entry, client, freezer
):
    """Rejected credentials require reauth rather than more fast boot checks."""
    media_id, _ = await setup_entry(hass, config_entry)
    await hass.services.async_call(
        "media_player", "turn_on", {"entity_id": media_id}, blocking=True
    )
    client.refresh.reset_mock()
    client.refresh.side_effect = SonyAuthenticationError("Registration revoked")
    freezer.tick(6)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert client.refresh.call_count == 1
    assert len(hass.config_entries.flow.async_progress()) == 1
    assert config_entry.runtime_data._wake_retries == 0


async def test_wol_network_failure_is_reported(hass, config_entry, legacy_http):
    """Sending errors reach the service caller, with no blind power-toggle fallback."""
    media_id, _ = await setup_entry(hass, config_entry)
    count = legacy_http.call_count
    with patch("wakeonlan.create_socket", side_effect=OSError("No broadcast route")):
        with pytest.raises(HomeAssistantError, match="wake packet"):
            await hass.services.async_call(
                "media_player", "turn_on", {"entity_id": media_id}, blocking=True
            )
    assert legacy_http.call_count == count
