"""Websocket API used by the Lovelace card.

Commands are registered once in ``async_setup`` and are non-admin (every HA user sees
every package). The subscription's ``forward`` callback re-resolves the manager on each
signal instead of capturing it, so a subscription opened before an entry reload keeps
working afterwards and reports ``loaded: false`` while the entry is down.

``extract`` exists so the card's paste dialog never goes through a service call: service
call data is written to the recorder as a ``call_service`` event, and pasted e-mails
should not be.
"""

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .carriers import CARRIERS, build_links, normalize as normalize_number
from .const import DOMAIN, ERR_NOT_LOADED, FALLBACK_NONE
from .helpers import SIGNAL_UPDATED, get_manager_or_none
from .manager import detect_number
from .services import async_extract_results


def _empty_snapshot() -> dict[str, Any]:
    return {
        "loaded": False,
        "today": dt_util.now().date().isoformat(),
        "packages": [],
        "counts": {"active": 0, "overdue": 0, "due_today": 0, "arrived": 0},
        "fallback_tracker": FALLBACK_NONE,
    }


def current_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    """Snapshot from the loaded entry, or the empty not-loaded snapshot."""
    manager = get_manager_or_none(hass)
    return manager.snapshot() if manager else _empty_snapshot()


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Send a snapshot now and after every change until unsubscribed."""
    msg_id = msg["id"]

    @callback
    def forward() -> None:
        connection.send_message(websocket_api.event_message(msg_id, current_snapshot(hass)))

    connection.subscriptions[msg_id] = async_dispatcher_connect(hass, SIGNAL_UPDATED, forward)
    connection.send_result(msg_id)
    forward()


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/history"})
@callback
def ws_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return archived packages, newest first."""
    if (manager := get_manager_or_none(hass)) is None:
        connection.send_error(msg["id"], ERR_NOT_LOADED, "Package Tracker is not loaded")
        return
    connection.send_result(msg["id"], {"packages": manager.history()})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/detect", vol.Required("tracking_number"): str}
)
@callback
def ws_detect(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Classify a number for the add form's detection chip."""
    raw: str = msg["tracking_number"]
    manager = get_manager_or_none(hass)
    fallback = manager.fallback if manager else FALLBACK_NONE
    canonical, candidates = detect_number(raw, hass.config.country)
    duplicate_of, in_history = (
        manager.lookup(canonical) if manager is not None and canonical else (None, None)
    )
    connection.send_result(
        msg["id"],
        {
            "normalized": normalize_number(raw),
            "canonical": canonical,
            "candidates": [
                {
                    **cand.as_dict(),
                    **build_links(cand.carrier, cand.tracking_number, fallback=fallback).as_dict(),
                }
                for cand in candidates
            ],
            "duplicate_of": duplicate_of,
            "in_history": in_history,
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/extract", vol.Required("text"): str}
)
@websocket_api.async_response
async def ws_extract(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Find tracking numbers in pasted text (truncated to the extraction cap)."""
    connection.send_result(msg["id"], {"results": await async_extract_results(hass, msg["text"])})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/carriers"})
@callback
def ws_carriers(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List the carrier registry in registry order."""
    connection.send_result(
        msg["id"],
        {
            "carriers": [
                {
                    "id": c.id,
                    "name": c.name,
                    "homepage": c.homepage,
                    "has_url": c.url_template is not None,
                }
                for c in CARRIERS.values()
            ]
        },
    )


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register every websocket command."""
    for command in (ws_subscribe, ws_history, ws_detect, ws_extract, ws_carriers):
        websocket_api.async_register_command(hass, command)
