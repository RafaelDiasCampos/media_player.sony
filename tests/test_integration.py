"""Load real HA platforms, update states, invoke services and unload."""

from unittest.mock import AsyncMock, call, patch

from homeassistant.components.media_player import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.sony.api import (
    SonyAuthenticationError,
    SonyConnectionError,
    SonyStatus,
)

from .conftest import COMMANDS, HOST


async def setup_entry(hass, config_entry):
    """Set up the actual integration and locate its two registered entities."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    media_id = registry.async_get_entity_id(
        "media_player", "sony", f"media_player.{HOST}_media_player"
    )
    remote_id = registry.async_get_entity_id("remote", "sony", f"remote.{HOST}_Remote")
    return media_id, remote_id


async def test_setup_update_and_unload(hass, client, config_entry):
    """Both entities follow polling, share one device and unload cleanly."""
    media_id, remote_id = await setup_entry(hass, config_entry)
    assert hass.states.get(media_id).state == "playing"
    assert hass.states.get(remote_id).state == "on"
    assert hass.states.get(media_id).attributes.get("media_duration") is None
    assert "Up" in hass.states.get(remote_id).attributes["commands"]
    device_registry = dr.async_get(hass)
    assert (
        len(dr.async_entries_for_config_entry(device_registry, config_entry.entry_id))
        == 1
    )
    registry = er.async_get(hass)
    assert (
        registry.async_get(media_id).device_id
        == registry.async_get(remote_id).device_id
    )

    client.refresh.return_value = SonyStatus(MediaPlayerState.OFF, COMMANDS)
    await config_entry.runtime_data.async_refresh()
    assert hass.states.get(media_id).state == "off"
    assert hass.states.get(remote_id).state == "off"
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert hass.states.get(media_id).state == "unavailable"


async def test_existing_entity_ids_are_preserved(hass, client, config_entry):
    """Upgrading version 2.0.0 must keep user-renamed entity IDs intact."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "media_player",
        "sony",
        f"media_player.{HOST}_media_player",
        suggested_object_id="my_home_theater",
        config_entry=config_entry,
    )
    registry.async_get_or_create(
        "remote",
        "sony",
        f"remote.{HOST}_Remote",
        suggested_object_id="my_sony_remote",
        config_entry=config_entry,
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("media_player.my_home_theater").state == "playing"
    assert hass.states.get("remote.my_sony_remote").state == "on"


async def test_device_without_mac(hass, client, config_entry):
    """A device without Wake-on-LAN still gets a valid, shared device entry."""
    client.device.mac = None
    media_id, remote_id = await setup_entry(hass, config_entry)
    assert hass.states.get(media_id).state == "playing"
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        identifier=("sony", HOST), config_entry_id=config_entry.entry_id
    )
    assert device is not None
    assert er.async_get(hass).async_get(remote_id).device_id == device.id


async def test_capabilities_match_commands(hass, client, config_entry):
    """Do not advertise unimplemented play_media, mute setters or activities."""
    client.refresh.return_value = SonyStatus(MediaPlayerState.ON, ("Power", "Play"))
    media_id, remote_id = await setup_entry(hass, config_entry)
    features = hass.states.get(media_id).attributes["supported_features"]
    assert features == (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.PLAY
    )
    assert hass.states.get(remote_id).attributes["supported_features"] == 0


@pytest.mark.parametrize(
    ("service", "command"),
    [
        ("media_play", "Play"),
        ("media_pause", "Pause"),
        ("media_stop", "Stop"),
        ("media_next_track", "Next"),
        ("media_previous_track", "Prev"),
        ("volume_up", "VolumeUp"),
        ("volume_down", "VolumeDown"),
    ],
)
async def test_media_services(hass, client, config_entry, service, command):
    """Execute media actions through Home Assistant's service registry."""
    media_id, _ = await setup_entry(hass, config_entry)
    await hass.services.async_call(
        "media_player", service, {"entity_id": media_id}, blocking=True
    )
    client.send_command.assert_called_once_with(command)


@pytest.mark.parametrize("domain", ["remote", "media_player"])
@pytest.mark.parametrize(("service", "power"), [("turn_on", True), ("turn_off", False)])
async def test_power_services(hass, client, config_entry, domain, service, power):
    """Power services have signatures compatible with the current HA platforms."""
    media_id, remote_id = await setup_entry(hass, config_entry)
    entity_id = remote_id if domain == "remote" else media_id
    await hass.services.async_call(
        domain, service, {"entity_id": entity_id}, blocking=True
    )
    client.set_power.assert_called_once_with(power)


async def test_remote_repeats_and_delay(hass, client, config_entry):
    """Repeat a full command sequence and use asynchronous delays between sends."""
    _, remote_id = await setup_entry(hass, config_entry)
    with patch("custom_components.sony.remote.asyncio.sleep", new=AsyncMock()) as sleep:
        await hass.services.async_call(
            "remote",
            "send_command",
            {
                "entity_id": remote_id,
                "command": ["Up", "Down"],
                "num_repeats": 2,
                "delay_secs": 0.2,
            },
            blocking=True,
        )
    assert client.send_command.call_args_list == [
        call("Up"),
        call("Down"),
        call("Up"),
        call("Down"),
    ]
    assert sleep.await_args_list == [call(0.2)] * 3


@pytest.mark.parametrize(
    "data", [{"command": ["Up", "Unknown"]}, {"command": "Up", "hold_secs": 1}]
)
async def test_invalid_remote_sequence_sends_nothing(hass, client, config_entry, data):
    """Reject an invalid sequence before any partial command is sent."""
    _, remote_id = await setup_entry(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "remote", "send_command", {"entity_id": remote_id, **data}, blocking=True
        )
    client.send_command.assert_not_called()


async def test_poll_failure_and_recovery(hass, client, config_entry):
    """A network failure is unavailable, then both entities recover together."""
    media_id, remote_id = await setup_entry(hass, config_entry)
    client.refresh.side_effect = SonyConnectionError("Offline")
    await config_entry.runtime_data.async_refresh()
    assert hass.states.get(media_id).state == "unavailable"
    assert hass.states.get(remote_id).state == "unavailable"
    client.refresh.side_effect = None
    client.refresh.return_value = SonyStatus(MediaPlayerState.PAUSED, COMMANDS)
    await config_entry.runtime_data.async_refresh()
    assert hass.states.get(media_id).state == "paused"
    assert hass.states.get(remote_id).state == "on"


async def test_initial_connection_failure_is_retried(hass, client, config_entry):
    """Let Home Assistant retry initial setup instead of creating broken entities."""
    config_entry.add_to_hass(hass)
    client.refresh.side_effect = SonyConnectionError("Offline")
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state == ConfigEntryState.SETUP_RETRY


async def test_initial_auth_failure_starts_reauth(hass, client, config_entry):
    """Authentication failures must not be converted into connection retries."""
    config_entry.add_to_hass(hass)
    client.refresh.side_effect = SonyAuthenticationError("Expired PIN")
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state == ConfigEntryState.SETUP_ERROR
    assert (
        hass.config_entries.flow.async_progress_by_handler("sony")[0]["context"][
            "source"
        ]
        == "reauth"
    )


async def test_command_errors_reach_service_caller(hass, client, config_entry):
    """Do not silently report a failed command as successful."""
    media_id, _ = await setup_entry(hass, config_entry)
    client.send_command.side_effect = SonyConnectionError("Offline")
    with pytest.raises(HomeAssistantError, match="Offline"):
        await hass.services.async_call(
            "media_player", "media_play", {"entity_id": media_id}, blocking=True
        )


async def test_command_authentication_failure_starts_reauth(hass, client, config_entry):
    """A rejected IRCC request should offer a repair flow for this entry."""
    media_id, _ = await setup_entry(hass, config_entry)
    client.send_command.side_effect = SonyAuthenticationError("Expired PIN")
    with pytest.raises(HomeAssistantError, match="authentication again"):
        await hass.services.async_call(
            "media_player", "media_play", {"entity_id": media_id}, blocking=True
        )
    await hass.async_block_till_done()
    flow = hass.config_entries.flow.async_progress_by_handler("sony")[0]
    assert flow["context"]["entry_id"] == config_entry.entry_id
    assert flow["context"]["source"] == "reauth"
