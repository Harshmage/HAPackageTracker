"""Package data model and status rules.

Deliberately free of Home Assistant imports: the status rules are the heart of the
integration (what is overdue, what is due today) and are unit-tested as plain
functions. Callers pass ``today`` in, always taken from HA's configured time zone, so
nothing here ever has to guess a time zone.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any

from .carriers import build_links, carrier_name, classify

CARRIER_SOURCE_AUTO = "auto"
CARRIER_SOURCE_MANUAL = "manual"


class Status(StrEnum):
    """Derived package status (never trusted from storage; recomputed on demand)."""

    NO_DATE = "no_date"
    IN_TRANSIT = "in_transit"
    DUE_TODAY = "due_today"
    OVERDUE = "overdue"
    ARRIVED = "arrived"
    ARCHIVED = "archived"


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    # Stored values are UTC; a naive value can only come from a hand-edited file.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(slots=True, kw_only=True)
class Package:
    """One tracked parcel as persisted in the store."""

    id: str
    tracking_number: str
    carrier: str = "unknown"
    carrier_source: str = CARRIER_SOURCE_AUTO
    description: str | None = None
    vendor: str | None = None
    notes: str | None = None
    expected_date: date | None = None
    expected_date_end: date | None = None
    tracking_url_override: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    added_by: str | None = None
    delivered_at: datetime | None = None
    archived_at: datetime | None = None
    # Persisted so status-transition events fire once, even across restarts.
    last_status: str = ""
    # {"due_today": "YYYY-MM-DD", "overdue": "YYYY-MM-DD"}: last digest that mentioned it.
    notified: dict[str, str] = field(default_factory=dict)

    @property
    def window_end(self) -> date | None:
        """Last day of the delivery window (the end date, or the single expected date)."""
        return self.expected_date_end or self.expected_date

    @property
    def is_archived(self) -> bool:
        """True once the package has moved to history."""
        return self.archived_at is not None

    @property
    def is_active(self) -> bool:
        """Neither arrived nor archived."""
        return self.delivered_at is None and self.archived_at is None

    def days_until(self, today: date) -> int | None:
        """Days until the end of the delivery window; negative when late."""
        end = self.window_end
        return (end - today).days if end is not None else None

    # Serialisation -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Storage representation (JSON-safe)."""
        return {
            "id": self.id,
            "tracking_number": self.tracking_number,
            "carrier": self.carrier,
            "carrier_source": self.carrier_source,
            "description": self.description,
            "vendor": self.vendor,
            "notes": self.notes,
            "expected_date": _iso(self.expected_date),
            "expected_date_end": _iso(self.expected_date_end),
            "tracking_url_override": self.tracking_url_override,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "added_by": self.added_by,
            "delivered_at": _iso(self.delivered_at),
            "archived_at": _iso(self.archived_at),
            "last_status": self.last_status,
            "notified": dict(self.notified),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Package | None:
        """Build a package from storage, ignoring unknown keys and bad values.

        Returns ``None`` only when the record lacks an id or a tracking number; every
        other field falls back to its default so an older or newer store still loads.
        """
        if not isinstance(data, dict):
            return None
        pkg_id = _opt_str(data.get("id"))
        number = _opt_str(data.get("tracking_number"))
        if not pkg_id or not number:
            return None
        now = datetime.now(UTC)
        source = data.get("carrier_source")
        notified = data.get("notified")
        return cls(
            id=pkg_id,
            tracking_number=number,
            carrier=_opt_str(data.get("carrier")) or "unknown",
            carrier_source=source
            if source in (CARRIER_SOURCE_AUTO, CARRIER_SOURCE_MANUAL)
            else CARRIER_SOURCE_AUTO,
            description=_opt_str(data.get("description")),
            vendor=_opt_str(data.get("vendor")),
            notes=_opt_str(data.get("notes")),
            expected_date=_parse_date(data.get("expected_date")),
            expected_date_end=_parse_date(data.get("expected_date_end")),
            tracking_url_override=_opt_str(data.get("tracking_url_override")),
            created_at=_parse_datetime(data.get("created_at")) or now,
            updated_at=_parse_datetime(data.get("updated_at")) or now,
            added_by=_opt_str(data.get("added_by")),
            delivered_at=_parse_datetime(data.get("delivered_at")),
            archived_at=_parse_datetime(data.get("archived_at")),
            last_status=str(data.get("last_status") or ""),
            notified={str(k): str(v) for k, v in notified.items() if isinstance(v, str)}
            if isinstance(notified, dict)
            else {},
        )

    # Presentation --------------------------------------------------------------

    def links(self, fallback: str):
        """Deep link, homepage and fallback links for this package."""
        return build_links(
            self.carrier,
            self.tracking_number,
            fallback=fallback,
            override=self.tracking_url_override,
        )

    def service(self) -> str | None:
        """Carrier service name, re-derived for auto-detected carriers only."""
        if self.carrier_source != CARRIER_SOURCE_AUTO:
            return None
        return _service_for(self.tracking_number, self.carrier)

    def as_api_dict(self, today: date, fallback: str) -> dict[str, Any]:
        """Representation for the card, services and websocket (contract section 2)."""
        links = self.links(fallback)
        return {
            "id": self.id,
            "tracking_number": self.tracking_number,
            "carrier": self.carrier,
            "carrier_name": carrier_name(self.carrier),
            "carrier_source": self.carrier_source,
            "service": self.service(),
            "description": self.description,
            "vendor": self.vendor,
            "notes": self.notes,
            "expected_date": _iso(self.expected_date),
            "expected_date_end": _iso(self.expected_date_end),
            "tracking_url_override": self.tracking_url_override,
            "tracking_url": links.tracking_url,
            "homepage_url": links.homepage_url,
            "fallback_url": links.fallback_url,
            "copy_only": links.copy_only,
            "status": str(compute_status(self, today)),
            "days_until": self.days_until(today),
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "delivered_at": _iso(self.delivered_at),
            "archived_at": _iso(self.archived_at),
        }

    def event_data(self, today: date, fallback: str) -> dict[str, Any]:
        """Bus event payload (contract section 5)."""
        links = self.links(fallback)
        return {
            "package_id": self.id,
            "tracking_number": self.tracking_number,
            "carrier": self.carrier,
            "carrier_name": carrier_name(self.carrier),
            "description": self.description,
            "vendor": self.vendor,
            "expected_date": _iso(self.expected_date),
            "expected_date_end": _iso(self.expected_date_end),
            "tracking_url": links.tracking_url or links.fallback_url,
            "status": str(compute_status(self, today)),
        }


@lru_cache(maxsize=512)
def _service_for(tracking_number: str, carrier: str) -> str | None:
    # Cached: snapshots re-render every package on every change.
    for cand in classify(tracking_number):
        if cand.carrier == carrier:
            return cand.service
    return None


def compute_status(pkg: Package, today: date) -> Status:
    """Return the package status for ``today`` (HA's local date).

    Order matters: archived beats arrived beats the date rules, and a delivery window
    only becomes overdue after its last day.
    """
    if pkg.archived_at is not None:
        return Status.ARCHIVED
    if pkg.delivered_at is not None:
        return Status.ARRIVED
    if pkg.expected_date is None:
        return Status.NO_DATE
    end = pkg.expected_date_end or pkg.expected_date
    if today > end:
        return Status.OVERDUE
    if pkg.expected_date <= today <= end:
        return Status.DUE_TODAY
    return Status.IN_TRANSIT
