"""Setup, unload, removal, card registration and startup ordering."""

import re
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.package_tracker.card_registration import (
    DATA_CARD,
    WWW_DIR,
    hash_directory,
)
from custom_components.package_tracker.const import ALL_EVENTS, DOMAIN, EVENT_OVERDUE, STORAGE_KEY
from custom_components.package_tracker.logbook import async_describe_events
from custom_components.package_tracker.manager import PackageManager

from .conftest import UPS, AddPackage

SERVICES = {
    "add_package",
    "update_package",
    "mark_arrived",
    "archive_package",
    "restore_package",
    "remove_package",
    "extract_tracking_numbers",
}
CARD_URL_RE = re.compile(r"^/package_tracker_static/[0-9a-f]{8}/package-tracker-card\.js$")


async def test_services_and_ws_exist_before_entry(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Services and websocket commands are registered by async_setup, without an entry."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    assert set(hass.services.async_services_for_domain(DOMAIN)) == SERVICES

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "package_tracker/carriers"})
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["carriers"][0]["id"] == "ups"

    await client.send_json_auto_id({"type": "package_tracker/subscribe"})
    msg = await client.receive_json()
    assert msg["success"]
    event = (await client.receive_json())["event"]
    assert event["loaded"] is False
    assert event["packages"] == []
    assert event["today"] == "2026-10-12"


async def test_setup_and_unload(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, PackageManager)
    assert hass.states.get("sensor.package_tracker_active_packages") is not None
    assert hass.states.get("calendar.package_tracker_deliveries") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_card_static_path_and_extra_js(hass: HomeAssistant, config_entry) -> None:
    """The card is served under a content-hash path and added to the frontend."""
    hass.config.components.add("frontend")
    with (
        patch("custom_components.package_tracker.card_registration.add_extra_js_url") as add_js,
        patch(
            "custom_components.package_tracker.card_registration.remove_extra_js_url"
        ) as remove_js,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        add_js.assert_called_once()
        url = add_js.call_args.args[1]
        assert CARD_URL_RE.match(url)
        assert hass.data[DATA_CARD].url == url

        key = url.split("/")[2]
        assert key == await hass.async_add_executor_job(hash_directory, WWW_DIR)

        # Served by aiohttp: the static route exists for the hashed base path.
        routes = {getattr(r.resource, "canonical", None) for r in hass.http.app.router.routes()}
        assert f"/package_tracker_static/{key}" in routes

        # Reload must not add the URL twice.
        assert await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()
        add_js.assert_called_once()

        assert await hass.config_entries.async_remove(config_entry.entry_id)
        await hass.async_block_till_done()
        remove_js.assert_called_once_with(hass, url)


async def test_card_not_added_without_frontend(hass: HomeAssistant, config_entry) -> None:
    with patch("custom_components.package_tracker.card_registration.add_extra_js_url") as add_js:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    add_js.assert_not_called()


async def test_card_static_file_served(
    hass: HomeAssistant, setup_integration, hass_client_no_auth
) -> None:
    client = await hass_client_no_auth()
    url = hass.data[DATA_CARD].url
    resp = await client.get(url)
    assert resp.status == 200
    assert "package-tracker-card" in await resp.text()


async def test_remove_entry_deletes_store(
    hass: HomeAssistant, setup_integration, add_package: AddPackage, hass_storage
) -> None:
    await add_package(UPS)
    assert STORAGE_KEY in hass_storage
    assert await hass.config_entries.async_remove(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert STORAGE_KEY not in hass_storage


async def test_startup_defers_transition_events(
    hass: HomeAssistant, config_entry, hass_storage
) -> None:
    """While HA is starting, overdue events wait for EVENT_HOMEASSISTANT_STARTED."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "packages": [
                {
                    "id": "p1",
                    "tracking_number": UPS,
                    "carrier": "ups",
                    "expected_date": "2026-10-11",
                    "last_status": "in_transit",
                }
            ],
            "meta": {},
        },
    }
    events = async_capture_events(hass, EVENT_OVERDUE)
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert events == []
    assert hass_storage[STORAGE_KEY]["data"]["packages"][0]["last_status"] == "in_transit"

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["package_id"] == "p1"
    assert events[0].data["status"] == "overdue"
    assert hass_storage[STORAGE_KEY]["data"]["packages"][0]["last_status"] == "overdue"


async def test_diagnostics_redacted(
    hass: HomeAssistant, setup_integration, add_package: AddPackage, hass_client
) -> None:
    await add_package(UPS, notes="Gate code 1234", tracking_url="https://example.com/t")
    assert await async_setup_component(hass, "diagnostics", {})
    # Diagnostics are fetched over HTTP, which needs an authenticated client.
    diag = await get_diagnostics_for_config_entry(hass, hass_client, setup_integration)
    pkg = diag["packages"][0]
    assert pkg["tracking_number"] == "**REDACTED**"
    assert pkg["notes"] == "**REDACTED**"
    assert pkg["tracking_url_override"] == "**REDACTED**"
    assert pkg["status"] == "no_date"
    assert diag["counts"]["active"] == 1


def test_logbook_describes_all_events() -> None:
    described: dict[str, object] = {}
    async_describe_events(None, lambda domain, event, cb: described.setdefault(event, cb))  # type: ignore[arg-type]
    assert set(described) == set(ALL_EVENTS)

    event = SimpleNamespace(
        event_type=EVENT_OVERDUE, data={"description": "Shoes", "carrier_name": "UPS"}
    )
    entry = described[EVENT_OVERDUE](event)  # type: ignore[operator]
    assert entry == {"name": "Shoes", "message": "(UPS) is overdue"}
