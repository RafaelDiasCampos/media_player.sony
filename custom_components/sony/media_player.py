"""Control a Sony device as a media player."""

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SonyConfigEntry, SonyCoordinator
from .entity import SonyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SonyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the media player from a config entry."""
    async_add_entities([SonyMediaPlayerEntity(entry.runtime_data)])


class SonyMediaPlayerEntity(SonyEntity, MediaPlayerEntity):
    """Represent a Sony media player using the shared coordinator state."""

    _attr_name = None

    def __init__(self, coordinator: SonyCoordinator) -> None:
        """Preserve the unique ID used by existing installations."""
        super().__init__(coordinator)
        host = coordinator.config_entry.data[CONF_HOST]
        self._attr_unique_id = f"media_player.{host}_media_player"

    @property
    def state(self) -> MediaPlayerState:
        """Read the current state directly, without a second cached state."""
        return self.coordinator.data.state

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Only advertise controls which the device actually provides."""
        commands = self.coordinator.data.commands
        features = MediaPlayerEntityFeature(0)
        for command, feature in (
            ("Play", MediaPlayerEntityFeature.PLAY),
            ("Pause", MediaPlayerEntityFeature.PAUSE),
            ("Stop", MediaPlayerEntityFeature.STOP),
            ("Next", MediaPlayerEntityFeature.NEXT_TRACK),
            ("Prev", MediaPlayerEntityFeature.PREVIOUS_TRACK),
        ):
            if command in commands:
                features |= feature
        if "VolumeUp" in commands and "VolumeDown" in commands:
            features |= MediaPlayerEntityFeature.VOLUME_STEP
        if "Power" in commands:
            features |= (
                MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
            )
        elif self.coordinator.client.device.mac:
            features |= MediaPlayerEntityFeature.TURN_ON
        return features

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        await self.coordinator.async_set_power(True)

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        await self.coordinator.async_set_power(False)

    async def _async_command(self, command: str) -> None:
        """Send a command, then update the state shared by both entities."""
        await self.coordinator.async_send_command(command)
        await self.coordinator.async_request_refresh()

    async def async_media_play(self) -> None:
        """Start playback."""
        await self._async_command("Play")

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self._async_command("Pause")

    async def async_media_stop(self) -> None:
        """Stop playback."""
        await self._async_command("Stop")

    async def async_media_next_track(self) -> None:
        """Skip to the next track."""
        await self._async_command("Next")

    async def async_media_previous_track(self) -> None:
        """Skip to the previous track."""
        await self._async_command("Prev")

    async def async_volume_up(self) -> None:
        """Increase volume by one step."""
        await self.coordinator.async_send_command("VolumeUp")

    async def async_volume_down(self) -> None:
        """Decrease volume by one step."""
        await self.coordinator.async_send_command("VolumeDown")
