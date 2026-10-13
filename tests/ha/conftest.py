"""Fixtures for the HA tests.

Every test runs in America/Chicago with the clock frozen at 2026-10-12 10:00 local, so
"today" is deterministic and the local date differs from the UTC date late in the day
(which is exactly where time-zone bugs hide).
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.package_tracker.config_flow import DEFAULT_OPTIONS
from custom_components.package_tracker.const import DOMAIN

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 12, 10, 0, 0, tzinfo=TZ)

UPS = "1Z999AA10123456784"
USPS = "9400109699939938223564"
FEDEX = "986578788855"
AMAZON = "TBA502887274000"


def local(*args: int) -> datetime:
    """Aware datetime in the test time zone."""
    return datetime(*args, tzinfo=TZ)


@pytest.fixture(autouse=True)
async def frozen_local_time(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Set the HA time zone and freeze the clock at 10:00 local."""
    await hass.config.async_set_time_zone("America/Chicago")
    freezer.move_to(NOW)


@pytest.fixture
def options() -> dict[str, Any]:
    """Entry options; override in a test module or parametrize to change them."""
    return dict(DEFAULT_OPTIONS)


@pytest.fixture
def config_entry(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    """A Package Tracker config entry added to hass (not set up)."""
    entry = MockConfigEntry(domain=DOMAIN, title="Package Tracker", data={}, options=options)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> MockConfigEntry:
    """Set up the entry and wait for startup work (transitions, catch-up) to finish."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


type AddPackage = Callable[..., Awaitable[dict[str, Any]]]


@pytest.fixture
def add_package(hass: HomeAssistant) -> AddPackage:
    """Call package_tracker.add_package and return the package API dict."""

    async def _add(tracking_number: str, **data: Any) -> dict[str, Any]:
        response = await hass.services.async_call(
            DOMAIN,
            "add_package",
            {"tracking_number": tracking_number, **data},
            blocking=True,
            return_response=True,
        )
        assert response is not None
        return response["package"]

    return _add
