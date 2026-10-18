"""Discover, configure and reconnect Sony Legacy API devices."""

import logging
from typing import Any

from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_REAUTH,
    SOURCE_USER,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
import requests
from sonyapilib.device import AuthenticationResult
import voluptuous as vol

from .api import (
    SonyAuthenticationError,
    SonyClient,
    SonyConnectionError,
    SonyDeviceMismatch,
)
from .const import (
    CONF_APP_PORT,
    CONF_COOKIES,
    CONF_DMR_PORT,
    CONF_IRCC_PORT,
    CONF_MAC_ADDRESS,
    CONF_PIN,
    CONF_UDN,
    DEFAULT_APP_PORT,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DMR_PORT,
    DEFAULT_IRCC_PORT,
    DOMAIN,
)
from .discovery import (
    async_ssdp_data,
    identity_matches,
    normalize_mac,
    normalize_udn,
    updated_address,
    valid_host,
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
    """Handle registration, discovery and address changes for the same device."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._client: SonyClient | None = None

    def _matching_entry(self, mac: str | None, udn: str | None) -> ConfigEntry | None:
        """Find older host-based entries as well as newer hardware-based entries."""
        for entry in self._async_current_entries(
            include_ignore=self.source != SOURCE_USER
        ):
            expected_mac = normalize_mac(entry.data.get(CONF_MAC_ADDRESS))
            observed_mac = normalize_mac(mac)
            if expected_mac and observed_mac and expected_mac != observed_mac:
                continue
            if identity_matches(
                entry.data.get(CONF_MAC_ADDRESS), entry.data.get(CONF_UDN), mac, udn
            ) or (
                entry.unique_id is not None
                and entry.unique_id
                in (
                    f"mac:{normalize_mac(mac)}" if normalize_mac(mac) else None,
                    normalize_udn(udn),
                )
            ):
                return entry
        return None

    async def _async_update_existing(self, entry: ConfigEntry) -> ConfigFlowResult:
        """Move an existing device, preserving its entry and registration."""
        await self.async_set_unique_id(entry.unique_id, raise_on_progress=False)
        if entry.source == SOURCE_IGNORE:
            return self.async_abort(reason="already_configured")
        data = updated_address(entry.data, self._data[CONF_HOST])
        if self._client is not None:
            if self._client.udn and not data.get(CONF_UDN):
                data[CONF_UDN] = self._client.udn
            if self._client.device.mac:
                data[CONF_MAC_ADDRESS] = self._client.device.mac
        changed = self.hass.config_entries.async_update_entry(entry, data=data)
        if changed and entry.state in (
            ConfigEntryState.LOADED,
            ConfigEntryState.SETUP_RETRY,
            ConfigEntryState.SETUP_ERROR,
        ):
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason="already_configured")

    async def _async_identify(self) -> ConfigFlowResult | None:
        """Deduplicate physical devices before requesting another pairing PIN."""
        assert self._client is not None
        mac, udn = self._client.device.mac, self._client.udn
        if entry := self._matching_entry(mac, udn):
            return await self._async_update_existing(entry)
        unique_id = (
            f"mac:{normalize_mac(mac)}" if normalize_mac(mac) else normalize_udn(udn)
        )
        await self.async_set_unique_id(unique_id or self._data[CONF_HOST])
        self._abort_if_unique_id_configured()
        return None

    def _save_client_data(self) -> None:
        """Keep credentials and observed hardware identity in the config entry."""
        assert self._client is not None
        self._data[CONF_PIN] = self._client.pin
        self._data[CONF_COOKIES] = self._client.cookies
        self._data[CONF_MAC_ADDRESS] = self._client.device.mac
        if self._client.udn:
            self._data[CONF_UDN] = self._client.udn

    async def _async_authenticate(
        self, step_id: str, schema: vol.Schema
    ) -> ConfigFlowResult:
        """Authenticate in an executor and only save successful registrations."""
        assert self._client is not None
        errors: dict[str, str] = {}
        try:
            await self.hass.async_add_executor_job(self._client.discover)
        except SonyDeviceMismatch:
            errors["base"] = "wrong_device"
        except SonyConnectionError, requests.RequestException, ValueError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error discovering the Sony device")
            errors["base"] = "unknown"
        if errors:
            return self.async_show_form(
                step_id=step_id,
                data_schema=schema,
                errors=errors,
                description_placeholders={
                    "name": self._data.get(CONF_NAME, DEFAULT_DEVICE_NAME),
                    "host": self._data[CONF_HOST],
                },
            )
        if self.source != SOURCE_REAUTH and (result := await self._async_identify()):
            return result
        try:
            result = await self.hass.async_add_executor_job(
                self._client.authenticate, self._data.get(CONF_PIN)
            )
        except SonyAuthenticationError:
            errors["base"] = "invalid_auth"
        except SonyConnectionError, requests.RequestException, ValueError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error connecting to the Sony device")
            errors["base"] = "unknown"
        else:
            if result == AuthenticationResult.PIN_NEEDED:
                return await self.async_step_pin()
            self._save_client_data()
            if self.source == SOURCE_REAUTH:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=self._data
                )
            return self.async_create_entry(
                title=self._data.get(CONF_NAME, DEFAULT_DEVICE_NAME), data=self._data
            )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "name": self._data.get(CONF_NAME, DEFAULT_DEVICE_NAME),
                "host": self._data[CONF_HOST],
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow manual setup when multicast discovery is unavailable."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )
        self._data = dict(user_input)
        self._data[CONF_HOST] = self._data[CONF_HOST].strip().lower()
        if not valid_host(self._data[CONF_HOST]):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={CONF_HOST: "invalid_host"},
            )
        if any(
            entry.data.get(CONF_HOST, "").strip().lower() == self._data[CONF_HOST]
            for entry in self._async_current_entries()
        ):
            return self.async_abort(reason="already_configured")
        self._client = SonyClient(self._data)
        return await self._async_authenticate("user", STEP_USER_DATA_SCHEMA)

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        """Offer discovered Sony devices and reconnect known hardware at a new IP."""
        try:
            self._data = await async_ssdp_data(self.hass, discovery_info)
            self._client = SonyClient(self._data)
            await self.hass.async_add_executor_job(self._client.discover)
        except ValueError:
            return self.async_abort(reason="invalid_discovery_info")
        except SonyConnectionError, requests.RequestException:
            return self.async_abort(reason="cannot_connect")
        except Exception:
            _LOGGER.exception("Unexpected error discovering a Sony device")
            return self.async_abort(reason="unknown")
        if result := await self._async_identify():
            return result
        self.context["title_placeholders"] = {
            "name": self._data[CONF_NAME],
            "host": self._data[CONF_HOST],
        }
        return await self.async_step_discovery_confirm()

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Use HA's observed MAC address to move registered devices, without scans."""
        mac = normalize_mac(discovery_info.macaddress)
        if not mac or not valid_host(discovery_info.ip):
            return self.async_abort(reason="invalid_discovery_info")
        if entry := self._matching_entry(mac, None):
            self._data = {CONF_HOST: discovery_info.ip}
            return await self._async_update_existing(entry)
        return self.async_abort(reason="not_configured")

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before starting registration on a newly discovered device."""
        schema = vol.Schema(
            {vol.Optional(CONF_NAME, default=self._data[CONF_NAME]): cv.string}
        )
        if user_input is not None:
            self._data.update(user_input)
            return await self._async_authenticate("discovery_confirm", schema)
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=schema,
            description_placeholders={
                "name": self._data[CONF_NAME],
                "host": self._data[CONF_HOST],
            },
        )

    async def async_step_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept the PIN displayed on the TV, preserving leading zeros."""
        if user_input is None:
            return self.async_show_form(step_id="pin", data_schema=STEP_PIN_DATA_SCHEMA)
        self._data[CONF_PIN] = user_input[CONF_PIN]
        return await self._async_authenticate("pin", STEP_PIN_DATA_SCHEMA)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Register again only when the device revokes saved credentials."""
        self._data = {**entry_data, CONF_PIN: "", CONF_COOKIES: []}
        self._client = SonyClient(self._data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        schema = vol.Schema({})
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm", data_schema=schema)
        return await self._async_authenticate("reauth_confirm", schema)
