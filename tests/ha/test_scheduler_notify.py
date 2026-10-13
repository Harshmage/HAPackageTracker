"""Midnight housekeeping, the daily digest, catch-up and arrival notifications."""

from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.package_tracker.config_flow import DEFAULT_OPTIONS
from custom_components.package_tracker.const import (
    DIGEST_NOTIFICATION_ID,
    DOMAIN,
    EVENT_ARCHIVED,
    EVENT_DUE_TODAY,
    EVENT_OVERDUE,
    EVENT_REMOVED,
    STORAGE_KEY,
)
from custom_components.package_tracker.scheduler import async_run_digest

from .conftest import FEDEX, UPS, USPS, AddPackage, local

NOTIFY_OPTIONS = {
    **DEFAULT_OPTIONS,
    "notify_entities": ["notify.phone"],
    "notify_services": ["mobile_app_x"],
}


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, *when: int) -> None:
    freezer.move_to(local(*when))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def store(packages: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> dict:
    return {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"packages": packages, "meta": meta or {}},
    }


@pytest.fixture
def notify_calls(hass: HomeAssistant) -> dict[str, list]:
    return {
        "send_message": async_mock_service(hass, "notify", "send_message"),
        "mobile_app_x": async_mock_service(hass, "notify", "mobile_app_x"),
    }


@pytest.fixture
def persistent():
    with patch("homeassistant.components.persistent_notification.async_create") as create:
        yield create


async def test_midnight_rollover_fires_overdue_once(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    freezer: FrozenDateTimeFactory,
) -> None:
    overdue = async_capture_events(hass, EVENT_OVERDUE)
    due = async_capture_events(hass, EVENT_DUE_TODAY)
    await add_package(UPS, expected_date="2026-10-12")
    await add_package(USPS, expected_date="2026-10-13")
    assert len(due) == 1
    assert overdue == []

    await tick(hass, freezer, 2026, 10, 12, 23, 59, 59)
    assert overdue == []

    await tick(hass, freezer, 2026, 10, 13, 0, 0, 5)
    assert len(overdue) == 1
    assert overdue[0].data["tracking_number"] == UPS
    assert len(due) == 2
    assert due[1].data["tracking_number"] == USPS
    assert hass.states.get("sensor.package_tracker_overdue_packages").state == "1"

    # Reloading re-runs the startup refresh; persisted last_status prevents a repeat.
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 2026, 10, 13, 12, 0, 0)
    assert len(overdue) == 1
    assert len(due) == 2

    # The next midnight: USPS becomes overdue, UPS does not fire again.
    await tick(hass, freezer, 2026, 10, 14, 0, 0, 5)
    assert [e.data["tracking_number"] for e in overdue] == [UPS, USPS]


@pytest.mark.parametrize("options", [NOTIFY_OPTIONS])
async def test_digest_sent_once_then_deduped(
    hass: HomeAssistant,
    notify_calls: dict[str, list],
    persistent,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    freezer: FrozenDateTimeFactory,
    hass_storage,
) -> None:
    await add_package(UPS, expected_date="2026-10-13", description="Shoes")
    await add_package(USPS, expected_date="2026-10-12")  # will be overdue tomorrow
    await add_package(FEDEX, expected_date="2026-10-01", description="Lamp")

    # Not yet 08:00 tomorrow: nothing sent (the 10:00 catch-up ran before these existed).
    await tick(hass, freezer, 2026, 10, 13, 7, 59, 59)
    assert notify_calls["send_message"] == []

    await tick(hass, freezer, 2026, 10, 13, 8, 0, 0)
    assert len(notify_calls["send_message"]) == 1
    assert len(notify_calls["mobile_app_x"]) == 1
    assert persistent.call_count == 1

    entity_call = notify_calls["send_message"][0]
    assert entity_call.data["entity_id"] == ["notify.phone"]
    message = entity_call.data["message"]
    assert "Due today" in message and "Overdue" in message
    assert "Shoes" in message and "Lamp" in message
    # Links are in the body because send_message has no data field.
    assert f"tracknum={UPS}" in message

    legacy = notify_calls["mobile_app_x"][0].data
    assert legacy["title"] == "Packages: 1 due today, 2 overdue"
    assert "data" not in legacy  # several packages: no single link to open

    _, kwargs = persistent.call_args
    assert kwargs["notification_id"] == DIGEST_NOTIFICATION_ID
    assert "](https://" in persistent.call_args.args[1]  # markdown links

    assert hass_storage[STORAGE_KEY]["data"]["meta"]["last_digest_date"] == "2026-10-13"

    # Same day again (e.g. options reload or a second timer): nothing new.
    manager = setup_integration.runtime_data
    assert await async_run_digest(manager) is False
    assert len(notify_calls["send_message"]) == 1

    # Next day: UPS (now overdue for the first time) is announced; the others were
    # already announced as overdue and remind_overdue_daily is off.
    await tick(hass, freezer, 2026, 10, 14, 8, 0, 0)
    assert len(notify_calls["mobile_app_x"]) == 2
    legacy = notify_calls["mobile_app_x"][1].data
    assert legacy["title"] == "Packages: 1 overdue"
    assert legacy["data"]["url"].endswith(UPS)
    assert legacy["data"]["clickAction"] == legacy["data"]["url"]

    # Day after: nothing qualifies, nothing is sent.
    await tick(hass, freezer, 2026, 10, 15, 8, 0, 0)
    assert len(notify_calls["mobile_app_x"]) == 2


@pytest.mark.parametrize("options", [{**NOTIFY_OPTIONS, "remind_overdue_daily": True}])
async def test_remind_overdue_daily(
    hass: HomeAssistant,
    notify_calls: dict[str, list],
    persistent,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    freezer: FrozenDateTimeFactory,
) -> None:
    await add_package(FEDEX, expected_date="2026-10-01")
    for day in (13, 14, 15):
        await tick(hass, freezer, 2026, 10, day, 8, 0, 0)
    assert len(notify_calls["mobile_app_x"]) == 3
    assert all(c.data["title"] == "Packages: 1 overdue" for c in notify_calls["mobile_app_x"])


@pytest.mark.parametrize(
    "options", [{**NOTIFY_OPTIONS, "notify_on": ["due_today"], "notify_time": "09:30:00"}]
)
async def test_notify_on_and_custom_time(
    hass: HomeAssistant,
    notify_calls: dict[str, list],
    persistent,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    freezer: FrozenDateTimeFactory,
) -> None:
    await add_package(FEDEX, expected_date="2026-10-01")
    await add_package(UPS, expected_date="2026-10-13")
    await tick(hass, freezer, 2026, 10, 13, 8, 0, 0)
    assert notify_calls["mobile_app_x"] == []
    await tick(hass, freezer, 2026, 10, 13, 9, 30, 0)
    assert len(notify_calls["mobile_app_x"]) == 1
    assert notify_calls["mobile_app_x"][0].data["title"] == "Packages: 1 due today"


@pytest.mark.parametrize("options", [NOTIFY_OPTIONS])
async def test_catch_up_on_start(
    hass: HomeAssistant,
    notify_calls: dict[str, list],
    persistent,
    config_entry: MockConfigEntry,
    hass_storage,
) -> None:
    """HA was down at 08:00; at 10:00 startup the digest goes out once."""
    hass_storage[STORAGE_KEY] = store(
        [{"id": "p1", "tracking_number": UPS, "carrier": "ups", "expected_date": "2026-10-12"}],
        {"last_digest_date": "2026-10-11"},
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(notify_calls["send_message"]) == 1
    assert len(notify_calls["mobile_app_x"]) == 1
    assert notify_calls["mobile_app_x"][0].data["data"]["url"].endswith(UPS)

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(notify_calls["send_message"]) == 1


@pytest.mark.parametrize("options", [{**NOTIFY_OPTIONS, "notify_time": "11:00:00"}])
async def test_no_catch_up_before_notify_time(
    hass: HomeAssistant,
    notify_calls: dict[str, list],
    persistent,
    config_entry: MockConfigEntry,
    hass_storage,
) -> None:
    hass_storage[STORAGE_KEY] = store(
        [{"id": "p1", "tracking_number": UPS, "carrier": "ups", "expected_date": "2026-10-12"}]
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert notify_calls["send_message"] == []


@pytest.mark.parametrize("options", [NOTIFY_OPTIONS])
async def test_failing_target_does_not_block_others(
    hass: HomeAssistant,
    persistent,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async_mock_service(hass, "notify", "send_message", raise_exception=HomeAssistantError("boom"))
    legacy = async_mock_service(hass, "notify", "mobile_app_x")
    await add_package(UPS, expected_date="2026-10-12")
    assert await async_run_digest(setup_integration.runtime_data) is True
    assert len(legacy) == 1
    assert persistent.call_count == 1
    assert "could not send notify.send_message" in caplog.text


@pytest.mark.parametrize(
    "options",
    [{**NOTIFY_OPTIONS, "notify_on": ["arrived"], "notify_services": [], "notify_entities": []}],
)
async def test_arrived_notification(
    hass: HomeAssistant, persistent, setup_integration: MockConfigEntry, add_package: AddPackage
) -> None:
    pkg = await add_package(UPS, description="Shoes")
    await hass.services.async_call(DOMAIN, "mark_arrived", {"package_id": pkg["id"]}, blocking=True)
    await hass.async_block_till_done()
    assert persistent.call_count == 1
    assert persistent.call_args.kwargs["title"] == "Package arrived"
    assert "Shoes" in persistent.call_args.args[1]


async def test_no_arrived_notification_by_default(
    hass: HomeAssistant, persistent, setup_integration: MockConfigEntry, add_package: AddPackage
) -> None:
    pkg = await add_package(UPS)
    await hass.services.async_call(DOMAIN, "mark_arrived", {"package_id": pkg["id"]}, blocking=True)
    await hass.async_block_till_done()
    persistent.assert_not_called()


@pytest.mark.parametrize("options", [{**DEFAULT_OPTIONS, "purge_after_days": 30}])
async def test_auto_archive_and_purge(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    hass_storage,
    freezer: FrozenDateTimeFactory,
) -> None:
    archived = async_capture_events(hass, EVENT_ARCHIVED)
    removed = async_capture_events(hass, EVENT_REMOVED)
    hass_storage[STORAGE_KEY] = store(
        [
            # Delivered 8 days ago: archived at startup.
            {"id": "old", "tracking_number": UPS, "delivered_at": "2026-10-04T15:00:00+00:00"},
            # Delivered 6 days ago (local): archived at the midnight that makes it 7.
            {"id": "recent", "tracking_number": USPS, "delivered_at": "2026-10-06T15:00:00+00:00"},
            # Archived 41 days ago: purged at startup.
            {"id": "stale", "tracking_number": FEDEX, "archived_at": "2026-09-01T15:00:00+00:00"},
            # Archived 29 days ago: kept until the next midnight.
            {
                "id": "kept",
                "tracking_number": "TBA502887274000",
                "archived_at": "2026-09-13T15:00:00+00:00",
            },
        ]
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    manager = config_entry.runtime_data
    assert [e.data["package_id"] for e in archived] == ["old"]
    assert [e.data["package_id"] for e in removed] == ["stale"]
    assert {p.id for p in manager.packages} == {"old", "recent", "kept"}

    await tick(hass, freezer, 2026, 10, 13, 0, 0, 5)
    assert [e.data["package_id"] for e in archived] == ["old", "recent"]
    assert [e.data["package_id"] for e in removed] == ["stale", "kept"]
    stored_ids = {p["id"] for p in hass_storage[STORAGE_KEY]["data"]["packages"]}
    assert stored_ids == {"old", "recent"}


@pytest.mark.parametrize("options", [{**DEFAULT_OPTIONS, "archive_after_days": 0}])
async def test_archive_disabled(
    hass: HomeAssistant, config_entry: MockConfigEntry, hass_storage
) -> None:
    hass_storage[STORAGE_KEY] = store(
        [{"id": "old", "tracking_number": UPS, "delivered_at": "2026-01-01T15:00:00+00:00"}]
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.runtime_data.history() == []
