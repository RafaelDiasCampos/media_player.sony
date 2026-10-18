"""Configure and reauthenticate a Sony Legacy API device."""

import logging
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers import config_validation as cv
import requests
from sonyapilib.device import AuthenticationResult
import voluptuous as vol

from .api import SonyAuthenticationError, SonyClient, SonyConnectionError
from .const import (
    CONF_APP_PORT,
    CONF_COOKIES,
    CONF_DMR_PORT,
    CONF_IRCC_PORT,
    CONF_MAC_ADDRESS,
    CONF_PIN,
    DEFAULT_APP_PORT,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DMR_PORT,
    DEFAULT_IRCC_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_DEVICE_NAME): cv.string,
        vol.Optional(CONF_PIN, default=""): cv.string,
        vol.Optional(CONF_APP_PORT, default=DEFAULT_APP_PORT): cv.port,
        vol.Optional(CONF_DMR_PORT, default=DEFAULT_DMR_PORT): cv.port,
        vol.Optional(CONF_IRCC_PORT, default=DEFAULT_IRCC_PORT): cv.port,
    }
)
STEP_PIN_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PIN): cv.string})


class SonyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle initial registration and expired Sony PINs."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Keep one client across the registration and PIN steps."""
        self._data: dict[str, Any] = {}
        self._client: SonyClient | None = None

    async def _async_authenticate(
        self, step_id: str, schema: vol.Schema
    ) -> ConfigFlowResult:
        """Authenticate in an executor and only save successful registrations."""
        assert self._client is not None
        errors: dict[str, str] = {}
        try:
            result = await self.hass.async_add_executor_job(
                self._client.authenticate, self._data.get(CONF_PIN)
            )
        except SonyAuthenticationError:
            errors["base"] = "invalid_auth"
        except SonyConnectionError, requests.RequestException, ValueError:
            errors["base"] = "cannot_connect"
        except Exception:  # Unexpected failures in the third-party protocol library.
            _LOGGER.exception("Unexpected error connecting to the Sony device")
            errors["base"] = "unknown"
        else:
            if result == AuthenticationResult.PIN_NEEDED:
                return await self.async_step_pin()
            self._data[CONF_PIN] = self._client.pin
            self._data[CONF_COOKIES] = self._client.cookies
            self._data[CONF_MAC_ADDRESS] = self._client.device.mac
            if self.source == SOURCE_REAUTH:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=self._data
                )
            return self.async_create_entry(
                title=self._data.get(CONF_NAME, DEFAULT_DEVICE_NAME), data=self._data
            )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the address and the device-specific ports."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )
        self._data = dict(user_input)
        self._data[CONF_HOST] = self._data[CONF_HOST].strip().lower()
        if not self._data[CONF_HOST] or any(
            char.isspace() or char in "/:?#@\\" for char in self._data[CONF_HOST]
        ):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={CONF_HOST: "invalid_host"},
            )
        await self.async_set_unique_id(self._data[CONF_HOST])
        self._abort_if_unique_id_configured()
        # Existing entries used the host verbatim as the unique ID.
        if any(
            entry.data[CONF_HOST].strip().lower() == self._data[CONF_HOST]
            for entry in self._async_current_entries()
        ):
            return self.async_abort(reason="already_configured")
        self._client = SonyClient(self._data)
        return await self._async_authenticate("user", STEP_USER_DATA_SCHEMA)

    async def async_step_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept the PIN displayed on the TV, preserving leading zeros."""
        if user_input is None:
            return self.async_show_form(step_id="pin", data_schema=STEP_PIN_DATA_SCHEMA)
        self._data[CONF_PIN] = user_input[CONF_PIN]
        return await self._async_authenticate("pin", STEP_PIN_DATA_SCHEMA)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start registration again when the device rejects saved credentials."""
        self._data = {**entry_data, CONF_PIN: "", CONF_COOKIES: []}
        self._client = SonyClient(self._data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Request a new PIN after the user turns on the Sony device."""
        schema = vol.Schema({})
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm", data_schema=schema)
        return await self._async_authenticate("reauth_confirm", schema)
