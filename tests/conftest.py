"""Fixtures using Home Assistant's actual integration test harness."""

from unittest.mock import MagicMock, patch

from homeassistant.components.media_player import MediaPlayerState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from sonyapilib.device import AuthenticationResult

from custom_components.sony.api import SonyClient, SonyStatus
from custom_components.sony.const import DOMAIN

HOST = "192.0.2.10"
MAC = "AA:BB:CC:DD:EE:FF"
COMMANDS = (
    "Power",
    "Play",
    "Pause",
    "Stop",
    "Next",
    "Prev",
    "VolumeUp",
    "VolumeDown",
    "Up",
    "Down",
    "Mute",
)
ENTRY_DATA = {
    "host": HOST,
    "app_port": 50202,
    "dmr_port": 52323,
    "ircc_port": 50001,
    "pin": "0123",
    "mac_address": MAC,
}


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow this repository's custom integration to be loaded."""


@pytest.fixture
def config_entry():
    """An entry saved by version 2.0.0, without the new optional name."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Sony Device",
        unique_id=HOST,
        data=ENTRY_DATA,
        version=1,
        minor_version=1,
    )


@pytest.fixture
def client():
    """Mock network operations, keeping the HA setup and entities real."""
    client = MagicMock(spec=SonyClient)
    client.device = MagicMock()
    client.device.host = HOST
    client.device.mac = MAC
    client.pin = "0123"
    client.model = "BDV-E2100"
    client.authenticate.return_value = AuthenticationResult.SUCCESS
    client.refresh.return_value = SonyStatus(MediaPlayerState.PLAYING, COMMANDS)
    with (
        patch("custom_components.sony.coordinator.SonyClient", return_value=client),
        patch("custom_components.sony.config_flow.SonyClient", return_value=client),
    ):
        yield client
