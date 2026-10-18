"""Set up the Sony Legacy API integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SonyConfigEntry, SonyCoordinator

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.REMOTE]


async def async_setup_entry(hass: HomeAssistant, entry: SonyConfigEntry) -> bool:
    """Connect to a Sony device and set up its entities."""
    coordinator = SonyCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SonyConfigEntry) -> bool:
    """Unload the entities and their coordinator."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
