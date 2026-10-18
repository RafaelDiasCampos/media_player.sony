"""Coordinate Sony polling and commands outside the event loop."""

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import requests

from .api import SonyAuthenticationError, SonyClient, SonyConnectionError, SonyStatus
from .const import CONF_COOKIES, DEVICE_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type SonyConfigEntry = ConfigEntry[SonyCoordinator]


class SonyCoordinator(DataUpdateCoordinator[SonyStatus]):
    """Share one connection and one polling schedule between both entities."""

    def __init__(self, hass: HomeAssistant, entry: SonyConfigEntry) -> None:
        """Initialize the coordinator without network I/O."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEVICE_SCAN_INTERVAL,
            always_update=False,
        )
        self.client = SonyClient(entry.data)
        self._lock = asyncio.Lock()

    async def _async_update_data(self) -> SonyStatus:
        """Fetch state in an executor, without exposing library internals."""
        async with self._lock:
            try:
                status = await self.hass.async_add_executor_job(self.client.refresh)
            except SonyAuthenticationError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (SonyConnectionError, requests.RequestException, ValueError) as err:
                raise UpdateFailed(str(err)) from err
            cookies = self.client.cookies
            if cookies != self.config_entry.data.get(CONF_COOKIES, []):
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_COOKIES: cookies},
                )
            return status

    async def _async_execute(self, method: Callable[..., Any], *args: Any) -> None:
        """Serialize commands with polling because the library stores mutable state."""
        async with self._lock:
            try:
                await self.hass.async_add_executor_job(method, *args)
            except SonyAuthenticationError as err:
                self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError("Sony requires authentication again") from err
            except (SonyConnectionError, requests.RequestException, ValueError) as err:
                raise HomeAssistantError(str(err)) from err

    async def async_send_command(self, command: str) -> None:
        """Send a command in an executor."""
        await self._async_execute(self.client.send_command, command)

    async def async_set_power(self, power_on: bool) -> None:
        """Set power and refresh both entities."""
        await self._async_execute(self.client.set_power, power_on)
        await self.async_request_refresh()
