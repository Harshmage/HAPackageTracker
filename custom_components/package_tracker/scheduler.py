"""Time-based work: the midnight tick, the daily digest and the startup catch-up.

Both timers use ``async_track_time_change`` (local wall-clock time), so they follow DST
and time-zone changes. The digest is deduplicated twice: per package via
``Package.notified`` (so a reminder is not repeated) and per day via
``meta.last_digest_date`` (so the startup catch-up does not resend a digest that already
went out today).
"""

from datetime import date, datetime, time
import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import notifications
from .const import (
    CONF_NOTIFY_ON,
    CONF_NOTIFY_TIME,
    CONF_REMIND_OVERDUE_DAILY,
    DEFAULT_NOTIFY_ON,
    DEFAULT_NOTIFY_TIME,
    DEFAULT_REMIND_OVERDUE_DAILY,
    MIDNIGHT_TICK,
    NOTIFY_DUE_TODAY,
    NOTIFY_OVERDUE,
)
from .manager import PackageManager, PackageTrackerConfigEntry
from .models import Package, Status, compute_status

_LOGGER = logging.getLogger(__name__)


def notify_time(options: dict) -> time:
    """Configured digest time; the TimeSelector stores a string, so parse it."""
    try:
        return cv.time(options.get(CONF_NOTIFY_TIME) or DEFAULT_NOTIFY_TIME)
    except vol.Invalid:
        _LOGGER.warning("Invalid notify_time %r, using default", options.get(CONF_NOTIFY_TIME))
        return cv.time(DEFAULT_NOTIFY_TIME)


@callback
def async_setup_scheduler(
    hass: HomeAssistant, entry: PackageTrackerConfigEntry, manager: PackageManager
) -> None:
    """Start both timers; their removers are tied to the entry's lifetime."""

    async def _midnight(_now: datetime) -> None:
        await manager.async_refresh_day()

    async def _digest(_now: datetime) -> None:
        await async_run_digest(manager)

    at = notify_time(dict(entry.options))
    entry.async_on_unload(async_track_time_change(hass, _midnight, **MIDNIGHT_TICK))
    entry.async_on_unload(
        async_track_time_change(hass, _digest, hour=at.hour, minute=at.minute, second=at.second)
    )


def digest_candidates(manager: PackageManager) -> dict[str, list[Package]]:
    """Packages the digest should mention right now, keyed by notify kind."""
    options = manager.entry.options
    notify_on = options.get(CONF_NOTIFY_ON, DEFAULT_NOTIFY_ON)
    remind = options.get(CONF_REMIND_OVERDUE_DAILY, DEFAULT_REMIND_OVERDUE_DAILY)
    today = manager.today
    today_iso = today.isoformat()
    due: list[Package] = []
    overdue: list[Package] = []
    for pkg in manager.packages:
        status = compute_status(pkg, today)
        if status is Status.DUE_TODAY and NOTIFY_DUE_TODAY in notify_on:
            if pkg.notified.get(NOTIFY_DUE_TODAY) != today_iso:
                due.append(pkg)
        elif status is Status.OVERDUE and NOTIFY_OVERDUE in notify_on:
            last = pkg.notified.get(NOTIFY_OVERDUE)
            if last is None or (remind and last != today_iso):
                overdue.append(pkg)

    def order(pkg: Package) -> tuple[date, datetime]:
        return (pkg.window_end or today, pkg.created_at)

    return {NOTIFY_DUE_TODAY: sorted(due, key=order), NOTIFY_OVERDUE: sorted(overdue, key=order)}


async def async_run_digest(manager: PackageManager) -> bool:
    """Send the digest if anything qualifies; returns True when one was sent."""
    options = dict(manager.entry.options)
    if not notifications.has_targets(options):
        return False
    kinds = digest_candidates(manager)
    if not any(kinds.values()):
        return False
    today = manager.today
    await notifications.async_send_digest(
        manager.hass,
        options,
        [manager.api(p, today) for p in kinds[NOTIFY_DUE_TODAY]],
        [manager.api(p, today) for p in kinds[NOTIFY_OVERDUE]],
    )
    await manager.async_mark_notified(kinds, today)
    return True


async def async_catch_up(manager: PackageManager) -> None:
    """Send today's digest if its time already passed while HA was down."""
    now = dt_util.now()
    if manager.meta.get("last_digest_date") == now.date().isoformat():
        return
    if now.time() >= notify_time(dict(manager.entry.options)):
        await async_run_digest(manager)
