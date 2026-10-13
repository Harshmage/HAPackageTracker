"""Websocket commands used by the card."""

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.package_tracker.const import DOMAIN

from .conftest import FEDEX, UPS, USPS, AddPackage


async def test_subscribe_snapshot_and_updates(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    hass_ws_client: WebSocketGenerator,
) -> None:
    pkg = await add_package(UPS, expected_date="2026-10-11")
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "package_tracker/subscribe"})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"] is None
    sub_id = msg["id"]

    msg = await client.receive_json()
    assert msg["id"] == sub_id and msg["type"] == "event"
    snap = msg["event"]
    assert snap["loaded"] is True
    assert snap["today"] == "2026-10-12"
    assert snap["fallback_tracker"] == "17track"
    assert snap["counts"] == {"active": 1, "overdue": 1, "due_today": 0, "arrived": 0}
    assert [p["id"] for p in snap["packages"]] == [pkg["id"]]
    assert snap["packages"][0]["status"] == "overdue"

    await hass.services.async_call(DOMAIN, "mark_arrived", {"package_id": pkg["id"]}, blocking=True)
    snap = (await client.receive_json())["event"]
    assert snap["counts"] == {"active": 0, "overdue": 0, "due_today": 0, "arrived": 1}

    await hass.services.async_call(
        DOMAIN, "archive_package", {"package_id": pkg["id"]}, blocking=True
    )
    snap = (await client.receive_json())["event"]
    assert snap["packages"] == []  # archived packages are history only

    # Unsubscribe stops events.
    await client.send_json_auto_id({"type": "unsubscribe_events", "subscription": sub_id})
    assert (await client.receive_json())["success"]
    await add_package(USPS)
    await client.send_json_auto_id({"type": "package_tracker/carriers"})
    assert (await client.receive_json())["type"] == "result"


async def test_subscription_survives_reload(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    hass_ws_client: WebSocketGenerator,
) -> None:
    await add_package(UPS)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "package_tracker/subscribe"})
    assert (await client.receive_json())["success"]
    assert (await client.receive_json())["event"]["loaded"] is True

    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()

    events = []
    # Drain everything sent during the reload; a final probe marks the end.
    await client.send_json_auto_id({"type": "package_tracker/carriers"})
    while (msg := await client.receive_json())["type"] == "event":
        events.append(msg["event"])
    assert any(e["loaded"] is False for e in events)
    assert events[-1]["loaded"] is True
    assert len(events[-1]["packages"]) == 1

    # Still live after the reload, with the new manager.
    await add_package(FEDEX)
    snap = (await client.receive_json())["event"]
    assert snap["loaded"] is True
    assert len(snap["packages"]) == 2


async def test_unload_sends_not_loaded(
    hass: HomeAssistant, setup_integration: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "package_tracker/subscribe"})
    await client.receive_json()
    await client.receive_json()
    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    snap = (await client.receive_json())["event"]
    assert snap["loaded"] is False
    assert snap["fallback_tracker"] == "none"

    await client.send_json_auto_id({"type": "package_tracker/history"})
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "not_loaded"


async def test_history(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    hass_ws_client: WebSocketGenerator,
    freezer,
) -> None:
    first = await add_package(UPS)
    second = await add_package(USPS)
    await add_package(FEDEX)
    await hass.services.async_call(
        DOMAIN, "archive_package", {"package_id": first["id"]}, blocking=True
    )
    freezer.tick(60)
    await hass.services.async_call(
        DOMAIN, "archive_package", {"package_id": second["id"]}, blocking=True
    )
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "package_tracker/history"})
    msg = await client.receive_json()
    assert msg["success"]
    packages = msg["result"]["packages"]
    assert [p["id"] for p in packages] == [second["id"], first["id"]]
    assert all(p["status"] == "archived" for p in packages)


async def test_detect(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    hass_ws_client: WebSocketGenerator,
) -> None:
    active = await add_package(UPS)
    archived = await add_package(USPS)
    await hass.services.async_call(
        DOMAIN, "archive_package", {"package_id": archived["id"]}, blocking=True
    )
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "package_tracker/detect", "tracking_number": "1z999aa1-0123-4567-84"}
    )
    result = (await client.receive_json())["result"]
    assert result["normalized"] == UPS
    assert result["canonical"] == UPS
    assert result["duplicate_of"] == active["id"]
    assert result["in_history"] is None
    cand = result["candidates"][0]
    assert cand["carrier"] == "ups"
    assert cand["checksum_valid"] is True
    assert cand["tracking_url"].endswith(UPS)
    assert cand["fallback_url"].endswith(UPS)
    assert cand["copy_only"] is False

    await client.send_json_auto_id({"type": "package_tracker/detect", "tracking_number": USPS})
    result = (await client.receive_json())["result"]
    assert result["duplicate_of"] is None
    assert result["in_history"] == archived["id"]

    await client.send_json_auto_id({"type": "package_tracker/detect", "tracking_number": "??"})
    result = (await client.receive_json())["result"]
    assert result == {
        "normalized": "",
        "canonical": "",
        "candidates": [],
        "duplicate_of": None,
        "in_history": None,
    }


async def test_detect_without_entry(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "package_tracker/detect", "tracking_number": UPS})
    result = (await client.receive_json())["result"]
    assert result["canonical"] == UPS
    assert result["duplicate_of"] is None
    assert result["candidates"][0]["fallback_url"] is None


async def test_extract_fires_no_call_service(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    add_package: AddPackage,
    hass_ws_client: WebSocketGenerator,
) -> None:
    pkg = await add_package(UPS)
    calls = async_capture_events(hass, EVENT_CALL_SERVICE)
    client = await hass_ws_client(hass)
    text = f"Your UPS tracking number is {UPS}.\nFedEx tracking: {FEDEX}\nCall 1-800-555-1212"
    await client.send_json_auto_id({"type": "package_tracker/extract", "text": text})
    msg = await client.receive_json()
    assert msg["success"]
    results = msg["result"]["results"]
    by_number = {r["tracking_number"]: r for r in results}
    assert by_number[UPS]["already_tracked"] is True
    assert by_number[UPS]["package_id"] == pkg["id"]
    assert by_number[FEDEX]["already_tracked"] is False
    await hass.async_block_till_done()
    assert calls == []


async def test_extract_long_text_truncated(
    hass: HomeAssistant, setup_integration: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    client = await hass_ws_client(hass)
    text = "hello world " * 10_000 + f"UPS tracking {UPS}"
    await client.send_json_auto_id({"type": "package_tracker/extract", "text": text})
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["results"] == []


async def test_carriers(
    hass: HomeAssistant, setup_integration: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "package_tracker/carriers"})
    carriers = (await client.receive_json())["result"]["carriers"]
    ids = [c["id"] for c in carriers]
    assert ids[:3] == ["ups", "usps", "fedex"]
    assert "unknown" in ids
    by_id = {c["id"]: c for c in carriers}
    assert by_id["ups"]["has_url"] is True
    assert by_id["unknown"]["has_url"] is False
    assert set(by_id["ups"]) == {"id", "name", "homepage", "has_url"}


async def test_non_admin_can_use_commands(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": "package_tracker/subscribe"})
    assert (await client.receive_json())["success"]
