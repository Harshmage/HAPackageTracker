"""Logbook descriptions for Package Tracker bus events."""

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
    LazyEventPartialState,
)
from homeassistant.core import HomeAssistant, callback

from .const import (
    ALL_EVENTS,
    DOMAIN,
    EVENT_ADDED,
    EVENT_ARCHIVED,
    EVENT_ARRIVED,
    EVENT_DUE_TODAY,
    EVENT_OVERDUE,
    EVENT_REMOVED,
)

_MESSAGES = {
    EVENT_ADDED: "was added",
    EVENT_DUE_TODAY: "is due today",
    EVENT_OVERDUE: "is overdue",
    EVENT_ARRIVED: "arrived",
    EVENT_ARCHIVED: "was archived",
    EVENT_REMOVED: "was removed",
}


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[
        [str, str, Callable[[LazyEventPartialState], dict[str, Any]]], None
    ],
) -> None:
    """Describe every Package Tracker event."""

    @callback
    def describe(event: LazyEventPartialState) -> dict[str, Any]:
        data = event.data
        name = data.get("description") or data.get("tracking_number") or "Package"
        carrier = data.get("carrier_name")
        message = _MESSAGES.get(event.event_type, event.event_type)
        if carrier:
            message = f"({carrier}) {message}"
        return {LOGBOOK_ENTRY_NAME: name, LOGBOOK_ENTRY_MESSAGE: message}

    for event_type in ALL_EVENTS:
        async_describe_event(DOMAIN, event_type, describe)
