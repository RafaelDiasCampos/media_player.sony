"""Adapt synchronous sonyapilib calls for Home Assistant.

All network methods run in an executor. Keep the library's stable client ID:
changing it would invalidate existing registrations.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import CONF_HOST
import requests
from sonyapilib.device import AuthenticationResult, HttpMethod, SonyDevice

from .const import (
    CONF_APP_PORT,
    CONF_DMR_PORT,
    CONF_IRCC_PORT,
    CONF_MAC_ADDRESS,
    CONF_PIN,
    DEFAULT_APP_PORT,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DMR_PORT,
    DEFAULT_IRCC_PORT,
)


class SonyConnectionError(Exception):
    """The device could not be reached or returned an invalid response."""


class SonyAuthenticationError(Exception):
    """The device rejected registration or saved credentials."""


@dataclass(frozen=True)
class SonyStatus:
    """The state shared by the media player and remote."""

    state: MediaPlayerState
    commands: tuple[str, ...]


class SonyClient:
    """Expose registration, polling and IRCC commands without hiding errors."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        """Create a client without performing network I/O."""
        self.device = SonyDevice(
            data[CONF_HOST],
            DEFAULT_DEVICE_NAME,
            app_port=data.get(CONF_APP_PORT, DEFAULT_APP_PORT),
            dmr_port=data.get(CONF_DMR_PORT, DEFAULT_DMR_PORT),
            ircc_port=data.get(CONF_IRCC_PORT, DEFAULT_IRCC_PORT),
        )
        self.device.mac = data.get(CONF_MAC_ADDRESS)
        self.pin = data.get(CONF_PIN)
        self.model: str | None = None
        self._discovered = False
        self._authenticated = False

    def _request(
        self, url: str, method: HttpMethod, **kwargs: Any
    ) -> requests.Response:
        """Make a request whose failures must reach Home Assistant."""
        try:
            response = self.device._send_http(
                url, method, raise_errors=True, log_errors=False, **kwargs
            )
        except requests.HTTPError as err:
            if err.response is not None and err.response.status_code in (401, 403):
                raise SonyAuthenticationError(
                    "Sony authentication was rejected"
                ) from err
            raise SonyConnectionError("Sony returned an HTTP error") from err
        except requests.RequestException as err:
            raise SonyConnectionError(
                "Cannot communicate with the Sony device"
            ) from err
        if response is None:
            raise SonyConnectionError("Sony returned no response")
        return response

    def authenticate(self, pin: str | None = None) -> AuthenticationResult:
        """Discover and register, optionally using the PIN displayed on the TV."""
        if not self._discovered:
            response = self._request(self.device.dmr_url, HttpMethod.GET)
            try:
                description = ElementTree.fromstring(response.text)
                self.model = description.findtext(
                    ".//{urn:schemas-upnp-org:device-1-0}modelName"
                )
                self.device.init_device()
            except (
                ValueError,
                KeyError,
                AttributeError,
                ElementTree.ParseError,
            ) as err:
                raise SonyConnectionError(
                    "Cannot read the Sony device description"
                ) from err
            if not self.device.control_url:
                raise SonyConnectionError(
                    "The device did not advertise an IRCC service"
                )
            self._discovered = True

        # Some older IRCC-only devices have no registration endpoint.
        if "register" not in self.device.actions:
            if not self.device.commands:
                raise SonyConnectionError(
                    "The device did not advertise remote commands"
                )
            result = AuthenticationResult.SUCCESS
        elif pin and pin != "0000":
            if not self.device.send_authentication(pin):
                raise SonyAuthenticationError("The Sony PIN was rejected")
            result = AuthenticationResult.SUCCESS
        else:
            result = self.device.register()

        if result == AuthenticationResult.ERROR:
            raise SonyConnectionError(
                "Sony registration failed; check remote access settings"
            )
        if result == AuthenticationResult.SUCCESS:
            # Scalar devices can accept registration without requesting a PIN.
            if self.device.api_version >= 4 and not self.device.commands:
                self.device._parse_command_list_v4()
            if not self.device.commands:
                raise SonyConnectionError("Cannot read the Sony remote command list")
            self.pin = str(self.device.pin) if self.device.pin is not None else None
            self._authenticated = True
        return result

    def _power_status(self) -> bool:
        """Keep standby, authentication and connection failures distinct."""
        if self.device.api_version < 4:
            # API version 0 has IRCC but no CERS action list.
            url = self.device.actionlist_url or self.device.dmr_url
            self._request(url, HttpMethod.GET)
            return True
        response = self._request(
            f"{self.device.base_url.rstrip('/')}/system",
            HttpMethod.POST,
            json=self.device._create_api_json("getPowerStatus"),
        )
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise SonyConnectionError("Sony returned an invalid power status")
            if error := payload.get("error"):
                if error[0] in (401, 403):
                    raise SonyAuthenticationError("Sony authentication was rejected")
                raise SonyConnectionError("Sony rejected the power status request")
            status = payload["result"][0]["status"]
            if status not in ("active", "standby", "off"):
                raise SonyConnectionError("Sony returned an unknown power status")
            return status == "active"
        except (ValueError, KeyError, IndexError, TypeError) as err:
            raise SonyConnectionError("Sony returned an invalid power status") from err

    def refresh(self) -> SonyStatus:
        """Return fresh device state, authenticating on the first update."""
        if not self._authenticated:
            if self.authenticate(self.pin) == AuthenticationResult.PIN_NEEDED:
                raise SonyAuthenticationError("A new Sony PIN is required")
        state = MediaPlayerState.OFF
        if self._power_status():
            state = MediaPlayerState.ON
            # AVTransport is optional, for example while using an HDMI input.
            if self.device.av_transport_url:
                try:
                    playback = self.device.get_playing_status()
                except ValueError, AttributeError, ElementTree.ParseError:
                    playback = None
                state = {
                    "PLAYING": MediaPlayerState.PLAYING,
                    "PAUSED_PLAYBACK": MediaPlayerState.PAUSED,
                    "STOPPED": MediaPlayerState.IDLE,
                }.get(playback, MediaPlayerState.ON)
        return SonyStatus(state, tuple(sorted(self.device.commands)))

    def send_command(self, command: str) -> None:
        """Send an advertised IRCC command and propagate HTTP failures."""
        if command not in self.device.commands:
            raise ValueError(f"Unsupported Sony command: {command}")
        code = escape(self.device.commands[command].value)
        self._request(
            self.device.control_url,
            HttpMethod.POST,
            headers={
                **self.device.headers,
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPACTION": '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"',
            },
            data=(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                '<s:Body><u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1">'
                f"<IRCCCode>{code}</IRCCCode>"
                "</u:X_SendIRCC></s:Body></s:Envelope>"
            ),
        )

    def set_power(self, power_on: bool) -> None:
        """Avoid toggling a device which is already in the requested state."""
        if power_on and self.device.mac:
            self.device.wakeonlan()
        try:
            is_on = self._power_status()
        except SonyConnectionError:
            if power_on and self.device.mac:
                # Wake-on-LAN was sent; polling will detect the device booting.
                return
            raise
        if power_on != is_on:
            if "Power" in self.device.commands:
                self.send_command("Power")
            elif not power_on or not self.device.mac:
                raise ValueError("The device does not advertise a power command")
