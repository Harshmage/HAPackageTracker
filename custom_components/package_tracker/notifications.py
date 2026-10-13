"""Digest and arrival notifications.

Named ``notifications.py`` rather than ``notify.py`` because a module called ``notify``
inside an integration is treated by Home Assistant as a notify *platform*.

Three delivery paths, all optional and independent:

* ``notify.send_message`` for notify entities. That service has no ``data`` field and
  some entities drop the title, so the links are written into the message body.
* Legacy ``notify.<service>`` (e.g. ``mobile_app_*``), which accepts ``data.url`` /
  ``data.clickAction`` so tapping the notification opens the tracking page.
* A persistent notification with a fixed id, so a new digest replaces the old one.

A failing target is logged and skipped; one broken phone must not block the others.
"""

from collections.abc import Iterable
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .const import (
    ARRIVED_NOTIFICATION_ID,
    CONF_NOTIFY_ENTITIES,
    CONF_NOTIFY_SERVICES,
    CONF_PERSISTENT_NOTIFICATION,
    DEFAULT_PERSISTENT_NOTIFICATION,
    DIGEST_NOTIFICATION_ID,
)

_LOGGER = logging.getLogger(__name__)

NOTIFY_DOMAIN = "notify"
SEND_MESSAGE = "send_message"


def _label(pkg: dict[str, Any]) -> str:
    name = pkg.get("description") or pkg.get("tracking_number") or "Package"
    if vendor := pkg.get("vendor"):
        name = f"{name} ({vendor})"
    return f"{name} - {pkg.get('carrier_name')}"


def _link(pkg: dict[str, Any]) -> str | None:
    return pkg.get("tracking_url") or pkg.get("fallback_url")


def _section(title: str, packages: list[dict[str, Any]], markdown: bool) -> list[str]:
    if not packages:
        return []
    lines = [f"**{title}**" if markdown else f"{title}:"]
    for pkg in packages:
        link = _link(pkg)
        if markdown and link:
            lines.append(f"- [{_label(pkg)}]({link})")
        elif link:
            lines.append(f"- {_label(pkg)}: {link}")
        else:
            lines.append(f"- {_label(pkg)}: {pkg.get('tracking_number')}")
    return lines


def build_digest(
    due_today: list[dict[str, Any]], overdue: list[dict[str, Any]], *, markdown: bool = False
) -> tuple[str, str]:
    """Return ``(title, message)`` for the daily digest (API dicts in)."""
    parts = []
    if due_today:
        parts.append(f"{len(due_today)} due today")
    if overdue:
        parts.append(f"{len(overdue)} overdue")
    title = "Packages: " + ", ".join(parts)
    lines = [
        *_section("Due today", due_today, markdown),
        *_section("Overdue", overdue, markdown),
    ]
    return title, "\n".join(lines)


async def async_deliver(
    hass: HomeAssistant,
    options: dict[str, Any],
    *,
    title: str,
    message: str,
    markdown_message: str,
    url: str | None,
    notification_id: str,
) -> None:
    """Send one notification to every configured target."""
    entities: list[str] = list(options.get(CONF_NOTIFY_ENTITIES) or [])
    if entities:
        await _async_call(
            hass,
            SEND_MESSAGE,
            # send_message may drop the title, so repeat it in the body.
            {"message": f"{title}\n{message}", "title": title},
            target={"entity_id": entities},
        )
    for service in _legacy_services(options.get(CONF_NOTIFY_SERVICES) or []):
        data: dict[str, Any] = {"message": message, "title": title}
        if url:
            data["data"] = {"url": url, "clickAction": url}
        await _async_call(hass, service, data)
    if options.get(CONF_PERSISTENT_NOTIFICATION, DEFAULT_PERSISTENT_NOTIFICATION):
        persistent_notification.async_create(
            hass, markdown_message, title=title, notification_id=notification_id
        )


def has_targets(options: dict[str, Any]) -> bool:
    """True when at least one delivery path is configured."""
    return bool(
        options.get(CONF_NOTIFY_ENTITIES)
        or options.get(CONF_NOTIFY_SERVICES)
        or options.get(CONF_PERSISTENT_NOTIFICATION, DEFAULT_PERSISTENT_NOTIFICATION)
    )


def _legacy_services(values: Iterable[str]) -> list[str]:
    # The options form allows typed values, so accept "notify.mobile_app_x" as well.
    out = []
    for value in values:
        name = str(value).strip().removeprefix(f"{NOTIFY_DOMAIN}.")
        if name and name not in (SEND_MESSAGE, "persistent_notification") and name not in out:
            out.append(name)
    return out


async def _async_call(
    hass: HomeAssistant,
    service: str,
    data: dict[str, Any],
    target: dict[str, Any] | None = None,
) -> None:
    try:
        await hass.services.async_call(NOTIFY_DOMAIN, service, data, blocking=True, target=target)
    except (HomeAssistantError, vol.Invalid) as err:  # schema errors are not HA errors
        _LOGGER.warning("Package Tracker could not send notify.%s: %s", service, err)


async def async_send_digest(
    hass: HomeAssistant,
    options: dict[str, Any],
    due_today: list[dict[str, Any]],
    overdue: list[dict[str, Any]],
) -> None:
    """Build and deliver the daily digest."""
    title, message = build_digest(due_today, overdue)
    _, markdown_message = build_digest(due_today, overdue, markdown=True)
    items = [*due_today, *overdue]
    url = _link(items[0]) if len(items) == 1 else None
    await async_deliver(
        hass,
        options,
        title=title,
        message=message,
        markdown_message=markdown_message,
        url=url,
        notification_id=DIGEST_NOTIFICATION_ID,
    )


async def async_send_arrived(
    hass: HomeAssistant, options: dict[str, Any], pkg: dict[str, Any]
) -> None:
    """Announce that a package was marked as arrived."""
    title = "Package arrived"
    await async_deliver(
        hass,
        options,
        title=title,
        message=_label(pkg),
        markdown_message=_label(pkg),
        url=_link(pkg),
        notification_id=ARRIVED_NOTIFICATION_ID,
    )
