"""Send Sony IRCC commands through the remote platform."""

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_HOLD_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    DEFAULT_NUM_REPEATS,
    RemoteEntity,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SonyConfigEntry, SonyCoordinator
from .entity import SonyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SonyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the remote from a config entry."""
    async_add_entities([SonyRemoteEntity(entry.runtime_data)])


class SonyRemoteEntity(SonyEntity, RemoteEntity):
    """A Sony remote which shares power state with its media player."""

    _attr_name = "Remote"
    _attr_icon = "mdi:remote-tv"

    def __init__(self, coordinator: SonyCoordinator) -> None:
        """Preserve the unique ID used by existing installations."""
        super().__init__(coordinator)
        host = coordinator.config_entry.data[CONF_HOST]
        self._attr_unique_id = f"remote.{host}_Remote"

    @property
    def is_on(self) -> bool:
        """Playing, paused and idle devices all count as powered on."""
        return self.coordinator.data.state != MediaPlayerState.OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the command names this particular device supports."""
        return {"commands": list(self.coordinator.data.commands)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        await self.coordinator.async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        await self.coordinator.async_set_power(False)

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send a repeatable sequence without blocking the event loop."""
        if kwargs.get(ATTR_HOLD_SECS, 0):
            raise ServiceValidationError("Sony IRCC does not support hold_secs")
        commands = (command,) if isinstance(command, str) else tuple(command)
        for name in commands:
            if name not in self.coordinator.data.commands:
                raise ServiceValidationError(f"Unsupported Sony command: {name}")
        repeats = kwargs.get(ATTR_NUM_REPEATS, DEFAULT_NUM_REPEATS)
        delay = kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS)
        sequence = commands * repeats
        for index, name in enumerate(sequence):
            if index and delay:
                await asyncio.sleep(delay)
            await self.coordinator.async_send_command(name)
        if sequence:
            await self.coordinator.async_request_refresh()
