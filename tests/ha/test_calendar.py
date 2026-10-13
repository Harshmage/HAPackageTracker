"""Deliveries calendar."""

from homeassistant.core import HomeAssistant
import pytest

from custom_components.package_tracker.const import DOMAIN

from .conftest import AMAZON, FEDEX, UPS, USPS, AddPackage

pytestmark = pytest.mark.usefixtures("setup_integration")

CALENDAR = "calendar.package_tracker_deliveries"


async def get_events(hass: HomeAssistant, start: str, end: str) -> list[dict]:
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": CALENDAR, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    return response[CALENDAR]["events"]


async def test_get_events_range_starting_mid_day(
    hass: HomeAssistant, add_package: AddPackage
) -> None:
    await add_package(UPS, expected_date="2026-10-12", description="Shoes")
    await add_package(USPS, expected_date="2026-10-13", expected_date_end="2026-10-15")
    await add_package(FEDEX, expected_date="2026-10-20")
    await add_package(AMAZON)  # no date: not on the calendar

    # Starts mid-day today (local), ends mid-day on the 14th.
    events = await get_events(hass, "2026-10-12 15:30:00", "2026-10-14 09:00:00")
    assert [e["summary"] for e in events] == ["Delivery: Shoes", f"Delivery: {USPS}"]
    assert events[0]["start"] == "2026-10-12"
    assert events[0]["end"] == "2026-10-13"
    assert events[0]["description"] == f"Tracking number: {UPS}\nCarrier: ups"
    assert events[1]["start"] == "2026-10-13"
    assert events[1]["end"] == "2026-10-16"

    # A range ending exactly at local midnight excludes that day.
    events = await get_events(hass, "2026-10-12 00:00:00", "2026-10-13 00:00:00")
    assert [e["summary"] for e in events] == ["Delivery: Shoes"]

    # A late-evening local range is still "today" even though it is tomorrow in UTC.
    events = await get_events(hass, "2026-10-13T03:00:00+00:00", "2026-10-13T04:30:00+00:00")
    assert [e["summary"] for e in events] == ["Delivery: Shoes"]


async def test_state_and_arrived_summary(hass: HomeAssistant, add_package: AddPackage) -> None:
    assert hass.states.get(CALENDAR).state == "off"
    pkg = await add_package(UPS, expected_date="2026-10-12", description="Shoes")
    state = hass.states.get(CALENDAR)
    assert state.state == "on"
    assert state.attributes["message"] == "Delivery: Shoes"
    assert state.attributes["all_day"] is True

    await hass.services.async_call(DOMAIN, "mark_arrived", {"package_id": pkg["id"]}, blocking=True)
    assert hass.states.get(CALENDAR).state == "off"
    events = await get_events(hass, "2026-10-12 00:00:00", "2026-10-13 00:00:00")
    assert events[0]["summary"] == "Arrived: Shoes"

    # Archived after arriving: still shown as history; archived undelivered: hidden.
    await hass.services.async_call(
        DOMAIN, "archive_package", {"package_id": pkg["id"]}, blocking=True
    )
    other = await add_package(USPS, expected_date="2026-10-12", description="Gone")
    await hass.services.async_call(
        DOMAIN, "archive_package", {"package_id": other["id"]}, blocking=True
    )
    events = await get_events(hass, "2026-10-12 00:00:00", "2026-10-13 00:00:00")
    assert [e["summary"] for e in events] == ["Arrived: Shoes"]


async def test_next_event_is_upcoming(hass: HomeAssistant, add_package: AddPackage) -> None:
    await add_package(UPS, expected_date="2026-10-01", description="Late")
    await add_package(USPS, expected_date="2026-10-14", description="Soon")
    state = hass.states.get(CALENDAR)
    assert state.state == "off"
    assert state.attributes["message"] == "Delivery: Soon"
    assert state.attributes["start_time"] == "2026-10-14 00:00:00"
