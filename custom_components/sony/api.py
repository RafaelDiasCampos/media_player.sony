"""Adapt synchronous sonyapilib calls for Home Assistant.

All network methods run in an executor. Keep the library's stable client ID:
changing it would invalidate existing registrations.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import math
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import CONF_HOST
import requests
from sonyapilib.device import AuthenticationResult, HttpMethod, SonyDevice, XmlApiObject

from .const import (
    CONF_APP_PORT,
    CONF_COOKIES,
    CONF_DMR_PORT,
    CONF_DMR_URL,
    CONF_IRCC_PORT,
    CONF_IRCC_URL,
    CONF_MAC_ADDRESS,
    CONF_PIN,
    CONF_UDN,
    DEFAULT_APP_PORT,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DMR_PORT,
    DEFAULT_IRCC_PORT,
)
from .discovery import identity_matches, normalize_mac, normalize_udn

_LOGGER = logging.getLogger(__name__)
_UPNP_DEVICE = "{urn:schemas-upnp-org:device-1-0}"
_UPNP_SERVICE = "{urn:schemas-upnp-org:service-1-0}"
_SOAP = "{http://schemas.xmlsoap.org/soap/envelope/}"


class SonyConnectionError(Exception):
    """The device could not be reached or returned an invalid response."""


class SonyAuthenticationError(Exception):
    """The device rejected registration or saved credentials."""


class SonyDeviceMismatch(SonyConnectionError):
    """The configured address now belongs to a different physical device."""


@dataclass(frozen=True)
class SonyStatus:
    """The state shared by the media player and remote."""

    state: MediaPlayerState
    commands: tuple[str, ...]
    volume_level: float | None = None
    is_volume_muted: bool | None = None
    can_set_volume: bool = False
    can_set_mute: bool = False


@dataclass(frozen=True)
class _RenderingControl:
    """Audio capabilities read from the advertised UPnP service description."""

    service_type: str
    control_url: str
    actions: frozenset[str]
    volume_min: int = 0
    volume_max: int = 0
    volume_step: int = 1

    @property
    def has_volume_range(self) -> bool:
        """Only normalize volume when the device supplies a usable range."""
        return (
            0 <= self.volume_min < self.volume_max <= 65535
            and 1 <= self.volume_step <= self.volume_max - self.volume_min
        )


class _SonyDevice(SonyDevice):
    """Retain cookies from legacy registration as well as Scalar registration."""

    def _send_http(self, url: str, method: HttpMethod, **kwargs: Any):
        response = super()._send_http(url, method, **kwargs)
        if response is not None and response.cookies:
            if self.cookies is None:
                self.cookies = requests.cookies.RequestsCookieJar()
            self.cookies.update(response.cookies)
        return response


class SonyClient:
    """Expose registration, polling and IRCC commands without hiding errors."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        """Create a client without performing network I/O."""
        self.device = _SonyDevice(
            data[CONF_HOST],
            DEFAULT_DEVICE_NAME,
            app_port=data.get(CONF_APP_PORT, DEFAULT_APP_PORT),
            dmr_port=data.get(CONF_DMR_PORT, DEFAULT_DMR_PORT),
            ircc_port=data.get(CONF_IRCC_PORT, DEFAULT_IRCC_PORT),
        )
        self.device.mac = data.get(CONF_MAC_ADDRESS)
        if url := data.get(CONF_DMR_URL):
            self.device.dmr_url = url
        if url := data.get(CONF_IRCC_URL):
            self.device.ircc_url = url
        self.device.cookies = requests.cookies.RequestsCookieJar()
        for cookie in data.get(CONF_COOKIES, []):
            self.device.cookies.set_cookie(requests.cookies.create_cookie(**cookie))
        self.pin = data.get(CONF_PIN)
        self.model: str | None = None
        self.udn: str | None = None
        self._expected_mac = normalize_mac(data.get(CONF_MAC_ADDRESS))
        self._expected_udn = normalize_udn(data.get(CONF_UDN))
        self._discovered = False
        self._authenticated = False
        self._rendering_control: _RenderingControl | None = None

    @property
    def cookies(self) -> list[dict[str, Any]]:
        """Return JSON-safe credentials, retaining cookie scope and expiry."""
        return [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
            }
            for cookie in self.device.cookies or ()
        ]

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

    def discover(self) -> None:
        """Read identity without credentials before trusting a changed address."""
        if self._discovered:
            return
        saved_cookies, saved_mac = self.device.cookies, self.device.mac
        self.device.cookies = requests.cookies.RequestsCookieJar()
        self.device.mac = None
        try:
            response = self._request(self.device.dmr_url, HttpMethod.GET)
            try:
                description = ElementTree.fromstring(response.text)
                self.model = description.findtext(
                    ".//{urn:schemas-upnp-org:device-1-0}modelName"
                )
                self.udn = normalize_udn(
                    description.findtext(".//{urn:schemas-upnp-org:device-1-0}UDN")
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
            observed_mac = normalize_mac(self.device.mac)
            if (
                (self._expected_mac and observed_mac)
                or (self._expected_udn and self.udn)
            ) and not identity_matches(
                self._expected_mac, self._expected_udn, observed_mac, self.udn
            ):
                raise SonyDeviceMismatch(
                    "The address belongs to a different Sony device"
                )
            self._discover_rendering_control(description)
            self._discovered = True
        finally:
            if saved_cookies:
                self.device.cookies.update(saved_cookies)
            if not self.device.mac:
                self.device.mac = saved_mac

    def _discover_rendering_control(self, description: ElementTree.Element) -> None:
        """Read optional audio capabilities without making IRCC depend on them."""
        for service in description.iter(f"{_UPNP_DEVICE}service"):
            service_type = service.findtext(f"{_UPNP_DEVICE}serviceType", "")
            if not service_type.startswith(
                "urn:schemas-upnp-org:service:RenderingControl:"
            ):
                continue
            control_url = service.findtext(f"{_UPNP_DEVICE}controlURL")
            scpd_url = service.findtext(f"{_UPNP_DEVICE}SCPDURL")
            if not control_url or not scpd_url:
                continue
            base_url = (
                description.findtext(f"{_UPNP_DEVICE}URLBase") or self.device.dmr_url
            )
            try:
                response = self._request(urljoin(base_url, scpd_url), HttpMethod.GET)
                scpd = ElementTree.fromstring(response.text)
                actions = frozenset(
                    item.text
                    for item in scpd.findall(
                        f"{_UPNP_SERVICE}actionList/{_UPNP_SERVICE}action/"
                        f"{_UPNP_SERVICE}name"
                    )
                    if item.text
                )
                minimum = maximum = 0
                step = 1
                for variable in scpd.findall(
                    f"{_UPNP_SERVICE}serviceStateTable/{_UPNP_SERVICE}stateVariable"
                ):
                    if variable.findtext(f"{_UPNP_SERVICE}name") != "Volume":
                        continue
                    volume_range = variable.find(f"{_UPNP_SERVICE}allowedValueRange")
                    if volume_range is not None:
                        try:
                            minimum = int(
                                volume_range.findtext(f"{_UPNP_SERVICE}minimum", "")
                            )
                            maximum = int(
                                volume_range.findtext(f"{_UPNP_SERVICE}maximum", "")
                            )
                            step = int(
                                volume_range.findtext(f"{_UPNP_SERVICE}step", "1")
                            )
                        except ValueError:
                            minimum = maximum = 0
                    break
                self._rendering_control = _RenderingControl(
                    service_type,
                    urljoin(base_url, control_url),
                    actions,
                    minimum,
                    maximum,
                    step,
                )
            except (
                SonyConnectionError,
                SonyAuthenticationError,
                ElementTree.ParseError,
            ) as err:
                _LOGGER.debug("Optional Sony audio description unavailable: %s", err)
            return

    def _rendering_request(
        self, action: str, arguments: Mapping[str, str] | None = None
    ) -> ElementTree.Element:
        """Call an advertised action with the SOAP encoding required by Sony."""
        service = self._rendering_control
        if service is None or action not in service.actions:
            raise ValueError(f"Unsupported Sony audio action: {action}")
        parameters = {"InstanceID": "0", "Channel": "Master", **(arguments or {})}
        body = "".join(
            f"<{name}>{escape(value)}</{name}>" for name, value in parameters.items()
        )
        try:
            response = self._request(
                service.control_url,
                HttpMethod.POST,
                headers={
                    **self.device.headers,
                    "Content-Type": 'text/xml; charset="utf-8"',
                    "SOAPACTION": f'"{service.service_type}#{action}"',
                },
                data=(
                    '<?xml version="1.0" encoding="utf-8"?>'
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                    f'<s:Body><u:{action} xmlns:u="{service.service_type}">'
                    f"{body}</u:{action}></s:Body></s:Envelope>"
                ),
            )
        except SonyAuthenticationError as err:
            # DLNA access control is separate from CERS PIN registration.
            raise SonyConnectionError(
                "Sony denied access to the audio service"
            ) from err
        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as err:
            raise SonyConnectionError("Sony returned invalid audio XML") from err
        result = root.find(f"{_SOAP}Body/{{{service.service_type}}}{action}Response")
        if result is None:
            raise SonyConnectionError(f"Sony audio action {action} failed")
        return result

    def _audio_status(self) -> tuple[float | None, bool | None]:
        """Treat optional audio failures independently from connection status."""
        service = self._rendering_control
        volume = None
        muted = None
        if service is None:
            return volume, muted
        if "GetVolume" in service.actions and service.has_volume_range:
            try:
                result = self._rendering_request("GetVolume")
                raw = int(result.findtext("{*}CurrentVolume", ""))
                if not service.volume_min <= raw <= service.volume_max:
                    raise ValueError("Volume outside the advertised range")
                volume = (raw - service.volume_min) / (
                    service.volume_max - service.volume_min
                )
            except (SonyConnectionError, SonyAuthenticationError, ValueError) as err:
                _LOGGER.debug("Optional Sony volume status unavailable: %s", err)
        if "GetMute" in service.actions:
            try:
                result = self._rendering_request("GetMute")
                raw_mute = result.findtext("{*}CurrentMute", "").strip().lower()
                if raw_mute not in ("0", "1", "true", "false"):
                    raise ValueError("Invalid mute state")
                muted = raw_mute in ("1", "true")
            except (SonyConnectionError, SonyAuthenticationError, ValueError) as err:
                _LOGGER.debug("Optional Sony mute status unavailable: %s", err)
        return volume, muted

    def set_volume_level(self, volume: float) -> None:
        """Set absolute volume using the device's advertised scale and step."""
        service = self._rendering_control
        if service is None or not service.has_volume_range:
            raise ValueError("Sony did not advertise a usable volume range")
        if not math.isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1")
        span = service.volume_max - service.volume_min
        steps = min(
            math.floor(volume * span / service.volume_step + 0.5),
            span // service.volume_step,
        )
        self._rendering_request(
            "SetVolume",
            {"DesiredVolume": str(service.volume_min + steps * service.volume_step)},
        )

    def mute_volume(self, mute: bool) -> None:
        """Set mute explicitly; never emulate a setter with a toggle command."""
        self._rendering_request("SetMute", {"DesiredMute": "1" if mute else "0"})

    def authenticate(
        self, pin: str | None = None, *, resume: bool = False
    ) -> AuthenticationResult:
        """Discover and register, optionally using the PIN displayed on the TV."""
        self.discover()

        if resume and "register" in self.device.actions:
            # A displayed PIN may be single-use. Check the registered client ID
            # and saved cookies without calling /register or opening a new PIN.
            try:
                self._load_commands()
            except SonyAuthenticationError:
                if not self.pin:
                    raise
                # Some older devices use a reusable Basic credential instead.
                # Retry the protected resource, never the registration endpoint.
                self.device.pin = self.pin
                self.device._recreate_authentication()
                self._load_commands()
            self._authenticated = True
            return AuthenticationResult.SUCCESS

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
            if "register" in self.device.actions:
                self._load_commands()
            if not self.device.commands:
                raise SonyConnectionError("Cannot read the Sony remote command list")
            self.pin = str(self.device.pin) if self.device.pin is not None else None
            self._authenticated = True
        return result

    def _load_commands(self) -> None:
        """Verify saved authorization against a fresh, protected command list."""
        action = self.device.actions.get("getRemoteCommandList")
        if action is None:
            raise SonyConnectionError("Cannot read the Sony remote command list")
        try:
            if self.device.api_version >= 4:
                response = self._request(
                    action.url,
                    HttpMethod.POST,
                    json=self.device._create_api_json(action.value),
                    headers={},
                )
                payload = response.json()
                if error := payload.get("error"):
                    if error[0] in (401, 403):
                        raise SonyAuthenticationError(
                            "Sony rejected the saved registration; pair again"
                        )
                    raise SonyConnectionError("Sony rejected the remote command list")
                commands = [XmlApiObject(item) for item in payload["result"][1]]
                for command in commands:
                    if command.name == "PowerOff":
                        command.name = "Power"
            else:
                response = self._request(action.url, HttpMethod.GET)
                commands = [
                    XmlApiObject(item.attrib)
                    for item in ElementTree.fromstring(response.text).iter("command")
                ]
            if not commands or any(
                not item.name or not item.value for item in commands
            ):
                raise SonyConnectionError("Cannot read the Sony remote command list")
            self.device.commands = {item.name: item for item in commands}
        except (
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
            ElementTree.ParseError,
            SonyConnectionError,
        ) as err:
            raise SonyConnectionError(
                "Cannot read the Sony remote command list"
            ) from err

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
            self.authenticate(resume=True)
        state = MediaPlayerState.OFF
        volume = None
        muted = None
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
            volume, muted = self._audio_status()
        service = self._rendering_control
        return SonyStatus(
            state=state,
            commands=tuple(sorted(self.device.commands)),
            volume_level=volume,
            is_volume_muted=muted,
            can_set_volume=volume is not None
            and service is not None
            and "SetVolume" in service.actions,
            can_set_mute=muted is not None
            and service is not None
            and "SetMute" in service.actions,
        )

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
        """Wake by MAC without a network probe or a racing power toggle."""
        if power_on and normalize_mac(self.device.mac):
            try:
                self.device.wakeonlan()
            except OSError as err:
                raise SonyConnectionError("Cannot send Sony wake packet") from err
            return
        is_on = self._power_status()
        if power_on != is_on:
            if "Power" in self.device.commands:
                self.send_command("Power")
            elif not power_on or not normalize_mac(self.device.mac):
                raise ValueError("The device does not advertise a power command")
