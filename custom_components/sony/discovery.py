"""Match physical Sony devices and update addresses without changing identity."""

from collections.abc import Mapping
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homeassistant.components import ssdp
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.service_info.ssdp import (
    ATTR_UPNP_FRIENDLY_NAME,
    SsdpServiceInfo,
)

from .const import (
    CONF_COOKIES,
    CONF_DMR_PORT,
    CONF_DMR_URL,
    CONF_ENTITY_ID_HOST,
    CONF_IRCC_PORT,
    CONF_IRCC_URL,
    DEFAULT_DEVICE_NAME,
    SSDP_IRCC,
    SSDP_MEDIA_RENDERER,
)


def normalize_mac(value: str | None) -> str | None:
    """Match colon, dash and DHCP MAC formats, rejecting empty/broadcast values."""
    if not isinstance(value, str):
        return None
    value = re.sub(r"[:.\-]", "", value.strip()).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", value) or value in ("0" * 12, "f" * 12):
        return None
    return ":".join(value[index : index + 2] for index in range(0, 12, 2))


def normalize_udn(value: str | None) -> str | None:
    """Normalize a UPnP device identity separately from its service suffix."""
    if not isinstance(value, str):
        return None
    return value.strip().lower().split("::", 1)[0] or None


def valid_host(host: str) -> bool:
    """Accept IPv4 addresses and hostnames supported by sonyapilib's URLs."""
    return bool(host) and not any(char.isspace() or char in "/:?#@\\" for char in host)


def identity_matches(
    expected_mac: str | None,
    expected_udn: str | None,
    mac: str | None,
    udn: str | None,
) -> bool:
    """Prefer an observed MAC, falling back to UPnP identity when MAC is absent."""
    expected_mac, mac = normalize_mac(expected_mac), normalize_mac(mac)
    if expected_mac and mac:
        return expected_mac == mac
    expected_udn, udn = normalize_udn(expected_udn), normalize_udn(udn)
    return bool(expected_udn and udn and expected_udn == udn)


def updated_address(data: Mapping[str, Any], host: str) -> dict[str, Any]:
    """Move endpoints and host-scoped cookies while freezing entity unique IDs."""
    old_host = data[CONF_HOST]
    updated = {**data, CONF_HOST: host}
    updated.setdefault(CONF_ENTITY_ID_HOST, old_host)
    for url_key, port_key in (
        (CONF_DMR_URL, CONF_DMR_PORT),
        (CONF_IRCC_URL, CONF_IRCC_PORT),
    ):
        if url := data.get(url_key):
            parsed = urlsplit(url)
            port = updated.get(port_key, parsed.port)
            updated[url_key] = urlunsplit(
                (
                    parsed.scheme,
                    f"{host}:{port}" if port else host,
                    parsed.path,
                    parsed.query,
                    "",
                )
            )
    if host != old_host and CONF_COOKIES in data:
        updated[CONF_COOKIES] = [
            {
                **cookie,
                "domain": host
                if cookie.get("domain", "").lstrip(".").lower() == old_host.lower()
                else cookie.get("domain", ""),
            }
            for cookie in data[CONF_COOKIES]
        ]
    return updated


async def async_ssdp_data(hass: HomeAssistant, info: SsdpServiceInfo) -> dict[str, Any]:
    """Use advertised description paths and ports instead of assuming defaults."""
    location = info.ssdp_location
    if not location:
        raise ValueError("Missing Sony description URL")
    parsed = urlsplit(location)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or not valid_host(host)
    ):
        raise ValueError("Unsupported Sony description URL")
    dmr_url = ircc_url = location
    # Some devices publish separate renderer and IRCC description documents.
    other_st = SSDP_MEDIA_RENDERER if info.ssdp_st == SSDP_IRCC else SSDP_IRCC
    for other in await ssdp.async_get_discovery_info_by_st(hass, other_st):
        if other.ssdp_location and urlsplit(other.ssdp_location).hostname == host:
            if other_st == SSDP_MEDIA_RENDERER:
                dmr_url = other.ssdp_location
            else:
                ircc_url = other.ssdp_location
            break
    return {
        CONF_HOST: host,
        CONF_NAME: info.upnp.get(ATTR_UPNP_FRIENDLY_NAME) or DEFAULT_DEVICE_NAME,
        CONF_DMR_PORT: urlsplit(dmr_url).port or 80,
        CONF_IRCC_PORT: urlsplit(ircc_url).port or 80,
        CONF_DMR_URL: dmr_url,
        CONF_IRCC_URL: ircc_url,
    }
