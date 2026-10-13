"""Persistence through HA's Store."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.package_tracker.const import DOMAIN, STORAGE_KEY

from .conftest import UPS, USPS, AddPackage


async def test_saved_after_each_mutation(
    hass: HomeAssistant, setup_integration: MockConfigEntry, add_package: AddPackage, hass_storage
) -> None:
    pkg = await add_package(UPS, description="Shoes", expected_date="2026-10-14")
    stored = hass_storage[STORAGE_KEY]
    assert stored["version"] == 1
    assert stored["minor_version"] == 1
    record = stored["data"]["packages"][0]
    assert record["id"] == pkg["id"]
    assert record["tracking_number"] == UPS
    assert record["expected_date"] == "2026-10-14"
    assert record["last_status"] == "in_transit"
    # Derived fields are never stored.
    assert "status" not in record
    assert "tracking_url" not in record

    await hass.services.async_call(DOMAIN, "mark_arrived", {"package_id": pkg["id"]}, blocking=True)
    assert hass_storage[STORAGE_KEY]["data"]["packages"][0]["delivered_at"] is not None


async def test_survives_reload(
    hass: HomeAssistant, setup_integration: MockConfigEntry, add_package: AddPackage
) -> None:
    await add_package(UPS, description="Shoes")
    await add_package(USPS)
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    manager = setup_integration.runtime_data
    assert sorted(p.tracking_number for p in manager.packages) == sorted([UPS, USPS])
    assert next(p for p in manager.packages if p.tracking_number == UPS).description == "Shoes"


async def test_loads_tolerantly(
    hass: HomeAssistant, config_entry: MockConfigEntry, hass_storage
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "packages": [
                {"id": "a", "tracking_number": UPS, "carrier": "ups", "added_in_v9": [1]},
                {"id": "b"},  # unreadable: skipped
                {"id": "a", "tracking_number": USPS},  # duplicate id: skipped
                "garbage",
            ],
            "meta": {"last_digest_date": "2026-10-11", "future": True},
            "future_top_level": 1,
        },
    }
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    manager = config_entry.runtime_data
    assert [p.id for p in manager.packages] == ["a"]
    assert manager.meta["last_digest_date"] == "2026-10-11"
