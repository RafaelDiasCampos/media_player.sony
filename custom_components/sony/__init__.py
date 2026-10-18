"""Set up the Sony Legacy API integration."""

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import SonyStatus
from .const import CONF_ENTITY_ID_HOST
from .coordinator import SonyConfigEntry, SonyCoordinator
from .discovery import normalize_mac

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.REMOTE]


async def async_setup_entry(hass: HomeAssistant, entry: SonyConfigEntry) -> bool:
    """Connect to a Sony device and set up its entities."""
    if CONF_ENTITY_ID_HOST not in entry.data:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ENTITY_ID_HOST: entry.data[CONF_HOST]}
        )
    coordinator = SonyCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if not normalize_mac(coordinator.client.device.mac):
            raise
        # Keep a saved device's wake controls usable while it is unreachable.
        # Failed connectivity remains unknown; it is not proof of standby.
        coordinator.data = SonyStatus(MediaPlayerState.OFF, ())
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SonyConfigEntry) -> bool:
    """Unload the entities and their coordinator."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
