"""PackageManager: the single owner of package state.

Every mutation goes through ``_async_commit``: recompute statuses (firing transition
events), save, call entity listeners, then signal websocket subscribers. Keeping that
order in one place is what guarantees the card, the entities and the store never
disagree.

Transition events (``due_today``/``overdue``) are deduplicated with the persisted
``last_status`` so a restart or reload never re-fires them. Until Home Assistant has
fully started they are held back (``last_status`` is left untouched) so automations that
are still loading do not miss them; ``async_start`` releases them.
"""

from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from . import notifications
from .carriers import CARRIERS, Candidate, canonicalize, classify, normalize
from .const import (
    CARRIER_AUTO,
    CARRIER_UNKNOWN,
    CONF_ARCHIVE_AFTER_DAYS,
    CONF_FALLBACK_TRACKER,
    CONF_NOTIFY_ON,
    CONF_PURGE_AFTER_DAYS,
    DEFAULT_ARCHIVE_AFTER_DAYS,
    DEFAULT_FALLBACK_TRACKER,
    DEFAULT_NOTIFY_ON,
    DEFAULT_PURGE_AFTER_DAYS,
    DOMAIN,
    ERR_DUPLICATE_PACKAGE,
    ERR_IN_HISTORY,
    ERR_INVALID_DATE_RANGE,
    ERR_INVALID_TRACKING_NUMBER,
    ERR_PACKAGE_NOT_FOUND,
    EVENT_ADDED,
    EVENT_ARCHIVED,
    EVENT_ARRIVED,
    EVENT_DUE_TODAY,
    EVENT_OVERDUE,
    EVENT_REMOVED,
    FALLBACK_NONE,
    FALLBACK_TRACKERS,
    NOTIFY_ARRIVED,
)
from .helpers import SIGNAL_UPDATED
from .models import CARRIER_SOURCE_AUTO, CARRIER_SOURCE_MANUAL, Package, Status, compute_status
from .store import PackageStore

_LOGGER = logging.getLogger(__name__)

type PackageTrackerConfigEntry = ConfigEntry[PackageManager]

_TRANSITION_EVENTS = {Status.DUE_TODAY: EVENT_DUE_TODAY, Status.OVERDUE: EVENT_OVERDUE}

# Service field name -> Package attribute for simple text/date fields.
_FIELD_MAP = {
    "description": "description",
    "vendor": "vendor",
    "notes": "notes",
    "expected_date": "expected_date",
    "expected_date_end": "expected_date_end",
    "tracking_url": "tracking_url_override",
}
_DATE_FIELDS = ("expected_date", "expected_date_end")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_option(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except TypeError, ValueError:
        return default


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


class PackageManager:
    """CRUD, status transitions, housekeeping and change notification."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: PackageStore) -> None:
        """Bind to the config entry; call ``async_load`` before use."""
        self.hass = hass
        self.entry = entry
        self._store = store
        self._packages: dict[str, Package] = {}
        self.meta: dict[str, Any] = {}
        self._listeners: list[CALLBACK_TYPE] = []
        self._events_enabled = False

    # Options -------------------------------------------------------------------

    @property
    def options(self) -> dict[str, Any]:
        """Raw config entry options (defaults applied by the accessors below)."""
        return dict(self.entry.options)

    @property
    def fallback(self) -> str:
        """Configured universal-tracker fallback."""
        value = self.entry.options.get(CONF_FALLBACK_TRACKER, DEFAULT_FALLBACK_TRACKER)
        return value if value in FALLBACK_TRACKERS else FALLBACK_NONE

    @property
    def archive_after_days(self) -> int:
        """Days after delivery before auto-archiving (0 = never)."""
        return _int_option(
            self.entry.options.get(CONF_ARCHIVE_AFTER_DAYS), DEFAULT_ARCHIVE_AFTER_DAYS
        )

    @property
    def purge_after_days(self) -> int:
        """Days after archiving before deleting from history (0 = never)."""
        return _int_option(self.entry.options.get(CONF_PURGE_AFTER_DAYS), DEFAULT_PURGE_AFTER_DAYS)

    @property
    def today(self) -> date:
        """Today in Home Assistant's time zone (never the browser's)."""
        return dt_util.now().date()

    # Lifecycle -----------------------------------------------------------------

    async def async_load(self) -> None:
        """Load packages from storage. Does not fire events or notify anyone."""
        packages, meta = await self._store.async_load()
        self._packages = {p.id: p for p in packages}
        self.meta = meta

    async def async_start(self) -> None:
        """Release transition events (HA has started) and run the daily housekeeping."""
        self._events_enabled = True
        await self.async_refresh_day()

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Register a callback run after every change; returns the remover."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    # Queries -------------------------------------------------------------------

    @property
    def packages(self) -> list[Package]:
        """All packages, including history."""
        return list(self._packages.values())

    def status(self, pkg: Package, today: date | None = None) -> Status:
        """Current status of ``pkg``."""
        return compute_status(pkg, today or self.today)

    def api(self, pkg: Package, today: date | None = None) -> dict[str, Any]:
        """Contract API dict for ``pkg``."""
        return pkg.as_api_dict(today or self.today, self.fallback)

    def get(self, package_id: str | None = None, tracking_number: str | None = None) -> Package:
        """Resolve a service target; raises ``package_not_found``.

        A tracking number prefers the active package, then the most recently archived.
        """
        if package_id is not None:
            if (pkg := self._packages.get(package_id)) is not None:
                return pkg
            raise _error(ERR_PACKAGE_NOT_FOUND, target=package_id)
        if tracking_number is not None:
            canonical = self._canonical(tracking_number)
            matches = [p for p in self._packages.values() if p.tracking_number == canonical]
            if matches:
                matches.sort(key=lambda p: (not p.is_archived, p.archived_at or p.created_at))
                return matches[-1]
            raise _error(ERR_PACKAGE_NOT_FOUND, target=tracking_number)
        raise _error(ERR_PACKAGE_NOT_FOUND, target="")

    def lookup(self, canonical: str, exclude: str | None = None) -> tuple[str | None, str | None]:
        """Return ``(active_package_id, archived_package_id)`` for a canonical number."""
        active: str | None = None
        archived: str | None = None
        for pkg in self._packages.values():
            if pkg.id == exclude or pkg.tracking_number != canonical:
                continue
            if pkg.is_archived:
                archived = pkg.id
            else:
                active = pkg.id
        return active, archived

    def tracked_map(self) -> dict[str, str]:
        """Canonical number -> package id for every non-archived package."""
        return {p.tracking_number: p.id for p in self._packages.values() if not p.is_archived}

    def counts(self, today: date | None = None) -> dict[str, int]:
        """Counts shown by the card and the sensors."""
        today = today or self.today
        counts = {"active": 0, "overdue": 0, "due_today": 0, "arrived": 0}
        for pkg in self._packages.values():
            status = compute_status(pkg, today)
            if status is Status.ARCHIVED:
                continue
            if status is Status.ARRIVED:
                counts["arrived"] += 1
                continue
            counts["active"] += 1
            if status is Status.OVERDUE:
                counts["overdue"] += 1
            elif status is Status.DUE_TODAY:
                counts["due_today"] += 1
        return counts

    def snapshot(self) -> dict[str, Any]:
        """Full websocket snapshot of active and arrived packages."""
        today = self.today
        return {
            "loaded": True,
            "today": today.isoformat(),
            "packages": [
                p.as_api_dict(today, self.fallback)
                for p in self._packages.values()
                if not p.is_archived
            ],
            "counts": self.counts(today),
            "fallback_tracker": self.fallback,
        }

    def history(self) -> list[dict[str, Any]]:
        """Archived packages, newest archived first."""
        today = self.today
        archived = [p for p in self._packages.values() if p.is_archived]
        archived.sort(key=lambda p: p.archived_at or p.created_at, reverse=True)
        return [p.as_api_dict(today, self.fallback) for p in archived]

    # Detection -----------------------------------------------------------------

    def _home_country(self) -> str | None:
        return self.hass.config.country

    def detect(self, raw: str) -> tuple[str, list[Candidate]]:
        """Return ``(canonical_number, candidates)``; canonical is ``""`` for junk."""
        return detect_number(raw, self._home_country())

    def _canonical(self, raw: str) -> str:
        return self.detect(raw)[0] or normalize(raw)

    # Mutations -----------------------------------------------------------------

    async def async_add(
        self,
        *,
        tracking_number: str,
        carrier: str = CARRIER_AUTO,
        description: str | None = None,
        vendor: str | None = None,
        notes: str | None = None,
        expected_date: date | None = None,
        expected_date_end: date | None = None,
        tracking_url: str | None = None,
        added_by: str | None = None,
        context: Context | None = None,
    ) -> tuple[Package, list[Candidate]]:
        """Add a package; raises translated errors for invalid or duplicate numbers."""
        canonical, candidates = self.detect(tracking_number)
        if not canonical:
            raise _error(ERR_INVALID_TRACKING_NUMBER)
        self._check_unique(canonical)
        _check_dates(expected_date, expected_date_end)
        if carrier in (None, CARRIER_AUTO) or carrier not in CARRIERS:
            carrier_id = candidates[0].carrier if candidates else CARRIER_UNKNOWN
            source = CARRIER_SOURCE_AUTO
        else:
            carrier_id, source = carrier, CARRIER_SOURCE_MANUAL
        now = dt_util.utcnow()
        pkg = Package(
            id=ulid_now(),
            tracking_number=canonical,
            carrier=carrier_id,
            carrier_source=source,
            description=_clean_text(description),
            vendor=_clean_text(vendor),
            notes=_clean_text(notes),
            expected_date=expected_date,
            expected_date_end=expected_date_end,
            tracking_url_override=_clean_text(tracking_url),
            created_at=now,
            updated_at=now,
            added_by=added_by,
        )
        self._packages[pkg.id] = pkg
        await self._async_commit(context, [(EVENT_ADDED, pkg)])
        return pkg, candidates

    async def async_update(
        self, pkg: Package, changes: dict[str, Any], context: Context | None = None
    ) -> Package:
        """Apply ``changes`` (service field names); ``None`` clears a field.

        ``carrier: auto`` (or ``None``) re-detects. Changing either date clears the
        digest bookkeeping so the new date is announced again.
        """
        new_number = pkg.tracking_number
        candidates: list[Candidate] | None = None
        if "tracking_number" in changes and changes["tracking_number"] is not None:
            new_number, candidates = self.detect(changes["tracking_number"])
            if not new_number:
                raise _error(ERR_INVALID_TRACKING_NUMBER)
            if new_number != pkg.tracking_number:
                self._check_unique(new_number, exclude=pkg.id)

        new_values = {
            attr: (changes[name] if name in _DATE_FIELDS else _clean_text(changes[name]))
            for name, attr in _FIELD_MAP.items()
            if name in changes
        }
        start = new_values.get("expected_date", pkg.expected_date)
        end = new_values.get("expected_date_end", pkg.expected_date_end)
        _check_dates(start, end)

        carrier = pkg.carrier
        source = pkg.carrier_source
        if "carrier" in changes:
            requested = changes["carrier"]
            if requested in (None, CARRIER_AUTO) or requested not in CARRIERS:
                source = CARRIER_SOURCE_AUTO
            else:
                carrier, source = requested, CARRIER_SOURCE_MANUAL
        if source == CARRIER_SOURCE_AUTO and (
            "carrier" in changes or new_number != pkg.tracking_number
        ):
            if candidates is None:
                candidates = self.detect(new_number)[1]
            carrier = candidates[0].carrier if candidates else CARRIER_UNKNOWN

        if start != pkg.expected_date or end != pkg.expected_date_end:
            pkg.notified = {}
        pkg.tracking_number = new_number
        pkg.carrier = carrier
        pkg.carrier_source = source
        for attr, value in new_values.items():
            setattr(pkg, attr, value)
        pkg.updated_at = dt_util.utcnow()
        await self._async_commit(context)
        return pkg

    async def async_set_arrived(
        self, pkg: Package, arrived: bool = True, context: Context | None = None
    ) -> None:
        """Mark (or unmark) a package as delivered; fires ``arrived`` on marking."""
        if arrived == (pkg.delivered_at is not None):
            return
        now = dt_util.utcnow()
        pkg.delivered_at = now if arrived else None
        pkg.updated_at = now
        await self._async_commit(context, [(EVENT_ARRIVED, pkg)] if arrived else [])
        if arrived and NOTIFY_ARRIVED in self.entry.options.get(CONF_NOTIFY_ON, DEFAULT_NOTIFY_ON):
            self.entry.async_create_task(
                self.hass,
                notifications.async_send_arrived(self.hass, self.options, self.api(pkg)),
                f"{DOMAIN} arrived notification",
            )

    async def async_archive(self, pkg: Package, context: Context | None = None) -> None:
        """Move a package to history."""
        if pkg.is_archived:
            return
        pkg.archived_at = pkg.updated_at = dt_util.utcnow()
        await self._async_commit(context, [(EVENT_ARCHIVED, pkg)])

    async def async_restore(self, pkg: Package, context: Context | None = None) -> None:
        """Bring a package back from history (refused if its number is active again)."""
        if not pkg.is_archived:
            return
        active, _ = self.lookup(pkg.tracking_number, exclude=pkg.id)
        if active is not None:
            raise _error(ERR_DUPLICATE_PACKAGE, tracking_number=pkg.tracking_number)
        pkg.archived_at = None
        pkg.updated_at = dt_util.utcnow()
        await self._async_commit(context)

    async def async_remove(self, pkg: Package, context: Context | None = None) -> None:
        """Delete a package permanently."""
        self._packages.pop(pkg.id, None)
        await self._async_commit(context, [(EVENT_REMOVED, pkg)])

    async def async_refresh_day(self) -> None:
        """Daily housekeeping: transitions, auto-archive and purge, then commit."""
        today = self.today
        events: list[tuple[str, Package]] = []
        if (days := self.archive_after_days) > 0:
            for pkg in self._packages.values():
                if pkg.delivered_at is None or pkg.is_archived:
                    continue
                if dt_util.as_local(pkg.delivered_at).date() + timedelta(days=days) <= today:
                    pkg.archived_at = dt_util.utcnow()
                    events.append((EVENT_ARCHIVED, pkg))
        if (days := self.purge_after_days) > 0:
            for pkg in list(self._packages.values()):
                if pkg.archived_at is None:
                    continue
                if dt_util.as_local(pkg.archived_at).date() + timedelta(days=days) <= today:
                    del self._packages[pkg.id]
                    events.append((EVENT_REMOVED, pkg))
        await self._async_commit(None, events)

    async def async_mark_notified(self, kinds: dict[str, list[Package]], today: date) -> None:
        """Record that a digest mentioned these packages (no listeners: not user-visible)."""
        for kind, packages in kinds.items():
            for pkg in packages:
                pkg.notified[kind] = today.isoformat()
        self.meta["last_digest_date"] = today.isoformat()
        await self._async_save()

    # Internals -----------------------------------------------------------------

    def _check_unique(self, canonical: str, exclude: str | None = None) -> None:
        active, archived = self.lookup(canonical, exclude=exclude)
        if active is not None:
            raise _error(ERR_DUPLICATE_PACKAGE, tracking_number=canonical)
        if archived is not None:
            raise _error(ERR_IN_HISTORY, tracking_number=canonical)

    async def _async_save(self) -> None:
        await self._store.async_save(list(self._packages.values()), self.meta)

    async def _async_commit(
        self, context: Context | None, events: list[tuple[str, Package]] | None = None
    ) -> None:
        today = self.today
        fallback = self.fallback
        transitions: list[tuple[str, Package]] = []
        for pkg in self._packages.values():
            status = compute_status(pkg, today)
            if not self._events_enabled or status == pkg.last_status:
                continue
            if (event_type := _TRANSITION_EVENTS.get(status)) is not None:
                transitions.append((event_type, pkg))
            pkg.last_status = status
        await self._async_save()
        for event_type, pkg in [*(events or []), *transitions]:
            self.hass.bus.async_fire(event_type, pkg.event_data(today, fallback), context=context)
        self._async_notify()

    @callback
    def _async_notify(self) -> None:
        for listener in list(self._listeners):
            listener()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)


def detect_number(raw: str, home_country: str | None) -> tuple[str, list[Candidate]]:
    """Return ``(canonical_number, candidates)`` for user input (number or carrier link)."""
    candidates = classify(raw, home_country=home_country)
    if candidates:
        return candidates[0].tracking_number, candidates
    normalized = normalize(raw)
    return (canonicalize(normalized) if normalized else ""), []


def _check_dates(start: date | None, end: date | None) -> None:
    if end is not None and (start is None or end < start):
        raise _error(ERR_INVALID_DATE_RANGE)
