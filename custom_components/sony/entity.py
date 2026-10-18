"""Common entity identity and device information."""

from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_DEVICE_NAME, DOMAIN
from .coordinator import SonyCoordinator


class SonyEntity(CoordinatorEntity[SonyCoordinator]):
    """An entity belonging to a Sony device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SonyCoordinator) -> None:
        """Keep media player and remote on the same device registry entry."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        device = coordinator.client.device
        host = entry.data[CONF_HOST]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.mac or host)},
            name=entry.data.get(CONF_NAME, DEFAULT_DEVICE_NAME),
            manufacturer="Sony",
            model=coordinator.client.model,
        )
        if device.mac:
            self._attr_device_info["connections"] = {
                (CONNECTION_NETWORK_MAC, device.mac)
            }
