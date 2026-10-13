"""Count sensors and the next-delivery date sensor."""

from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.package_tracker.const import DOMAIN, MAX_SENSOR_PACKAGES
from custom_components.package_tracker.sensor import PackageCountSensor

from .conftest import AMAZON, FEDEX, UPS, USPS, AddPackage

pytestmark = pytest.mark.usefixtures("setup_integration")

ACTIVE = "sensor.package_tracker_active_packages"
OVERDUE = "sensor.package_tracker_overdue_packages"
DUE_TODAY = "sensor.package_tracker_packages_due_today"
NEXT = "sensor.package_tracker_next_delivery"


async def test_initial_state(hass: HomeAssistant) -> None:
    state = hass.states.get(ACTIVE)
    assert state.state == "0"
    assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "packages"
    assert state.attributes["packages"] == []
    assert hass.states.get(NEXT).state == "unknown"
    assert hass.states.get(NEXT).attributes[ATTR_DEVICE_CLASS] == "date"


async def test_counts_follow_changes(hass: HomeAssistant, add_package: AddPackage) -> None:
    overdue = await add_package(UPS, expected_date="2026-10-10", description="Late")
    await add_package(USPS, expected_date="2026-10-12")
    await add_package(FEDEX, expected_date="2026-10-15")
    arrived = await add_package(AMAZON)

    assert hass.states.get(ACTIVE).state == "4"
    await hass.services.async_call(
        DOMAIN, "mark_arrived", {"package_id": arrived["id"]}, blocking=True
    )
    assert hass.states.get(ACTIVE).state == "3"
    assert hass.states.get(OVERDUE).state == "1"
    assert hass.states.get(DUE_TODAY).state == "1"
    attrs = hass.states.get(OVERDUE).attributes["packages"]
    assert attrs[0]["id"] == overdue["id"]
    assert attrs[0]["description"] == "Late"
    assert attrs[0]["status"] == "overdue"
    assert "notes" not in attrs[0]

    # Window that started yesterday counts as today for "next delivery".
    assert hass.states.get(NEXT).state == "2026-10-12"
    await hass.services.async_call(
        DOMAIN, "archive_package", {"tracking_number": USPS}, blocking=True
    )
    assert hass.states.get(NEXT).state == "2026-10-15"
    assert hass.states.get(NEXT).attributes["packages"][0]["tracking_number"] == FEDEX
    assert hass.states.get(DUE_TODAY).state == "0"


async def test_packages_attribute_capped_and_unrecorded(
    hass: HomeAssistant, add_package: AddPackage
) -> None:
    for i in range(MAX_SENSOR_PACKAGES + 3):
        await add_package(f"PKG{i:08d}X")
    state = hass.states.get(ACTIVE)
    assert state.state == str(MAX_SENSOR_PACKAGES + 3)
    assert len(state.attributes["packages"]) == MAX_SENSOR_PACKAGES

    assert "packages" in PackageCountSensor._unrecorded_attributes


async def test_device_and_unique_ids(
    hass: HomeAssistant,
    setup_integration,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    device = device_registry.async_get_device({(DOMAIN, setup_integration.entry_id)})
    assert device is not None
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    entities = er.async_entries_for_config_entry(entity_registry, setup_integration.entry_id)
    assert {e.unique_id for e in entities} == {
        f"{setup_integration.entry_id}_{key}"
        for key in ("active", "overdue", "due_today", "next_delivery", "deliveries")
    }
    assert all(e.device_id == device.id for e in entities)
