"""Service actions: CRUD lifecycle, events and translated errors."""

from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events
import voluptuous as vol

from custom_components.package_tracker.const import (
    ALL_EVENTS,
    DOMAIN,
    EVENT_ADDED,
    EVENT_ARCHIVED,
    EVENT_ARRIVED,
    EVENT_DUE_TODAY,
    EVENT_OVERDUE,
    EVENT_REMOVED,
)

from .conftest import AMAZON, FEDEX, UPS, USPS, AddPackage

pytestmark = pytest.mark.usefixtures("setup_integration")


async def call(hass: HomeAssistant, service: str, **data: Any) -> Any:
    return await hass.services.async_call(DOMAIN, service, data, blocking=True)


async def call_response(hass: HomeAssistant, service: str, **data: Any) -> Any:
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True
    )


def all_events(hass: HomeAssistant) -> dict[str, list]:
    return {event_type: async_capture_events(hass, event_type) for event_type in ALL_EVENTS}


async def test_add_detects_carrier_and_returns_candidates(hass: HomeAssistant) -> None:
    events = all_events(hass)
    response = await call_response(
        hass,
        "add_package",
        tracking_number="1z 999 aa1 0123 4567 84",
        description="Headphones",
        vendor="Shop",
        notes="Leave at side door",
        expected_date="2026-10-14",
    )
    pkg = response["package"]
    assert pkg["tracking_number"] == UPS
    assert pkg["carrier"] == "ups"
    assert pkg["carrier_source"] == "auto"
    assert pkg["status"] == "in_transit"
    assert pkg["days_until"] == 2
    assert pkg["expected_date"] == "2026-10-14"
    assert pkg["tracking_url"].endswith(UPS)
    assert pkg["fallback_url"] == f"https://t.17track.net/en#nums={UPS}"
    assert response["candidates"][0]["carrier"] == "ups"

    assert len(events[EVENT_ADDED]) == 1
    data = events[EVENT_ADDED][0].data
    assert data["package_id"] == pkg["id"]
    assert data["carrier_name"] == "UPS"
    assert data["description"] == "Headphones"
    assert data["status"] == "in_transit"


async def test_add_without_response(hass: HomeAssistant) -> None:
    assert await call(hass, "add_package", tracking_number=FEDEX) is None
    manager = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert [p.tracking_number for p in manager.packages] == [FEDEX]


async def test_add_manual_carrier(hass: HomeAssistant, add_package: AddPackage) -> None:
    pkg = await add_package("ABC12345678", carrier="dhl")
    assert pkg["carrier"] == "dhl"
    assert pkg["carrier_source"] == "manual"
    assert pkg["service"] is None


async def test_add_unknown_number(hass: HomeAssistant, add_package: AddPackage) -> None:
    pkg = await add_package("ZZ-not-a-real-format")
    assert pkg["carrier"] == "unknown"
    assert pkg["tracking_number"] == "ZZNOTAREALFORMAT"
    assert pkg["tracking_url"] is None
    assert pkg["copy_only"] is True
    assert pkg["fallback_url"] is not None


async def test_add_due_today_fires_transition(hass: HomeAssistant, add_package: AddPackage) -> None:
    events = all_events(hass)
    await add_package(UPS, expected_date="2026-10-12")
    await add_package(USPS, expected_date="2026-10-10")
    assert len(events[EVENT_DUE_TODAY]) == 1
    assert len(events[EVENT_OVERDUE]) == 1
    # A later unrelated mutation does not re-fire them.
    await add_package(FEDEX)
    assert len(events[EVENT_DUE_TODAY]) == 1
    assert len(events[EVENT_OVERDUE]) == 1


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({"tracking_number": "!!!"}, "invalid_tracking_number"),
        (
            {
                "tracking_number": UPS,
                "expected_date": "2026-10-14",
                "expected_date_end": "2026-10-13",
            },
            "invalid_date_range",
        ),
        ({"tracking_number": UPS, "expected_date_end": "2026-10-13"}, "invalid_date_range"),
    ],
)
async def test_add_errors(hass: HomeAssistant, data: dict, key: str) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "add_package", **data)
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == key


async def test_duplicate_and_in_history(hass: HomeAssistant, add_package: AddPackage) -> None:
    pkg = await add_package(UPS)
    with pytest.raises(ServiceValidationError) as err:
        await add_package(UPS.lower())
    assert err.value.translation_key == "duplicate_package"
    assert err.value.translation_placeholders == {"tracking_number": UPS}

    await call(hass, "archive_package", package_id=pkg["id"])
    with pytest.raises(ServiceValidationError) as err:
        await add_package(UPS)
    assert err.value.translation_key == "in_history"


async def test_invalid_url_rejected(hass: HomeAssistant) -> None:
    with pytest.raises(vol.Invalid):
        await call(hass, "add_package", tracking_number=UPS, tracking_url="javascript:alert(1)")


async def test_target_validation(hass: HomeAssistant) -> None:
    with pytest.raises(vol.Invalid):
        await call(hass, "archive_package")
    with pytest.raises(vol.Invalid):
        await call(hass, "archive_package", package_id="x", tracking_number=UPS)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "archive_package", package_id="missing")
    assert err.value.translation_key == "package_not_found"
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "mark_arrived", tracking_number=UPS)
    assert err.value.translation_key == "package_not_found"


async def test_update_fields_and_clear(hass: HomeAssistant, add_package: AddPackage) -> None:
    pkg = await add_package(
        UPS, description="Old", vendor="V", notes="n", tracking_url="https://example.com/t"
    )
    response = await call_response(
        hass,
        "update_package",
        package_id=pkg["id"],
        description="New",
        vendor=None,
        tracking_url=None,
        expected_date="2026-10-13",
        expected_date_end="2026-10-15",
    )
    updated = response["package"]
    assert updated["description"] == "New"
    assert updated["vendor"] is None
    assert updated["notes"] == "n"
    assert updated["tracking_url_override"] is None
    assert updated["tracking_url"].startswith("https://www.ups.com/")
    assert updated["expected_date_end"] == "2026-10-15"
    assert updated["status"] == "in_transit"

    # Target by tracking number alone.
    await call(hass, "update_package", tracking_number=UPS, notes="")
    manager = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert manager.get(pkg["id"]).notes is None

    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "update_package", package_id=pkg["id"], expected_date_end="2026-10-01")
    assert err.value.translation_key == "invalid_date_range"


async def test_update_number_and_carrier(hass: HomeAssistant, add_package: AddPackage) -> None:
    pkg = await add_package(UPS)
    other = await add_package(FEDEX)
    # With package_id, tracking_number is the new number; carrier re-detects.
    response = await call_response(
        hass, "update_package", package_id=pkg["id"], tracking_number=USPS
    )
    assert response["package"]["tracking_number"] == USPS
    assert response["package"]["carrier"] == "usps"

    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "update_package", package_id=pkg["id"], tracking_number=FEDEX)
    assert err.value.translation_key == "duplicate_package"

    response = await call_response(hass, "update_package", package_id=other["id"], carrier="dhl")
    assert response["package"]["carrier"] == "dhl"
    assert response["package"]["carrier_source"] == "manual"
    response = await call_response(hass, "update_package", package_id=other["id"], carrier="auto")
    assert response["package"]["carrier"] == "fedex"
    assert response["package"]["carrier_source"] == "auto"


async def test_date_change_clears_notified(hass: HomeAssistant, add_package: AddPackage) -> None:
    pkg = await add_package(UPS, expected_date="2026-10-12")
    manager = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    manager.get(pkg["id"]).notified = {"due_today": "2026-10-12"}
    await call(hass, "update_package", package_id=pkg["id"], description="x")
    assert manager.get(pkg["id"]).notified == {"due_today": "2026-10-12"}
    await call(hass, "update_package", package_id=pkg["id"], expected_date="2026-10-13")
    assert manager.get(pkg["id"]).notified == {}


async def test_arrived_archive_restore_remove(hass: HomeAssistant, add_package: AddPackage) -> None:
    events = all_events(hass)
    pkg = await add_package(AMAZON, expected_date="2026-10-11")
    manager = hass.config_entries.async_entries(DOMAIN)[0].runtime_data

    await call(hass, "mark_arrived", package_id=pkg["id"])
    assert manager.api(manager.get(pkg["id"]))["status"] == "arrived"
    assert len(events[EVENT_ARRIVED]) == 1
    assert events[EVENT_ARRIVED][0].data["status"] == "arrived"
    # Marking twice is a no-op; unmarking fires nothing.
    await call(hass, "mark_arrived", package_id=pkg["id"])
    await call(hass, "mark_arrived", package_id=pkg["id"], arrived=False)
    assert len(events[EVENT_ARRIVED]) == 1
    assert manager.api(manager.get(pkg["id"]))["status"] == "overdue"

    await call(hass, "archive_package", tracking_number=AMAZON)
    assert len(events[EVENT_ARCHIVED]) == 1
    assert manager.history()[0]["id"] == pkg["id"]
    assert manager.snapshot()["packages"] == []

    await call(hass, "restore_package", package_id=pkg["id"])
    assert manager.history() == []
    assert manager.snapshot()["packages"][0]["id"] == pkg["id"]

    await call(hass, "remove_package", package_id=pkg["id"])
    assert len(events[EVENT_REMOVED]) == 1
    assert manager.packages == []


async def test_restore_refused_when_number_active(
    hass: HomeAssistant, add_package: AddPackage
) -> None:
    pkg = await add_package(UPS)
    await call(hass, "archive_package", package_id=pkg["id"])
    manager = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    # Simulate an active copy of the same number (e.g. from a hand-edited store).
    clone, _ = await manager.async_add(tracking_number=FEDEX)
    clone.tracking_number = UPS
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "restore_package", package_id=pkg["id"])
    assert err.value.translation_key == "duplicate_package"


async def test_event_context_user(hass: HomeAssistant, hass_admin_user) -> None:
    events = async_capture_events(hass, EVENT_ADDED)
    context = Context(user_id=hass_admin_user.id)
    await hass.services.async_call(
        DOMAIN, "add_package", {"tracking_number": UPS}, blocking=True, context=context
    )
    assert events[0].context is context
    manager = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert manager.packages[0].added_by == hass_admin_user.id


async def test_extract_service(hass: HomeAssistant, add_package: AddPackage) -> None:
    await add_package(UPS)
    response = await call_response(
        hass,
        "extract_tracking_numbers",
        text=f"Your UPS tracking number is {UPS}. Order #123456789012.",
    )
    results = response["results"]
    assert [r["tracking_number"] for r in results] == [UPS]
    assert results[0]["already_tracked"] is True
    assert results[0]["package_id"] is not None


async def test_extract_requires_response(hass: HomeAssistant) -> None:
    with pytest.raises(ServiceValidationError):
        await call(hass, "extract_tracking_numbers", text="x")


async def test_not_loaded_error(hass: HomeAssistant, setup_integration) -> None:
    await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "add_package", tracking_number=UPS)
    assert err.value.translation_key == "not_loaded"
    # Extraction is pure and keeps working.
    response = await call_response(hass, "extract_tracking_numbers", text=f"UPS tracking: {UPS}")
    assert response["results"][0]["already_tracked"] is False
