"""The "Deliveries" calendar: one all-day event per package with an expected date.

Calendar ranges arrive as aware datetimes while package dates are plain dates; comparing
the two raises ``TypeError``, so the range is converted to local dates first.
"""

from datetime import date, datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import ONE_DAY
from .entity import PackageTrackerEntity
from .manager import PackageTrackerConfigEntry
from .models import Package

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PackageTrackerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the calendar."""
    async_add_entities([DeliveriesCalendar(entry, "deliveries")])


def _event(pkg: Package) -> CalendarEvent | None:
    if pkg.expected_date is None:
        return None
    # Archived-but-never-delivered packages were abandoned; keep them off the calendar.
    if pkg.is_archived and pkg.delivered_at is None:
        return None
    name = pkg.description or pkg.tracking_number
    prefix = "Arrived" if pkg.delivered_at is not None else "Delivery"
    details = [f"Tracking number: {pkg.tracking_number}", f"Carrier: {pkg.carrier}"]
    if pkg.vendor:
        details.insert(0, f"Vendor: {pkg.vendor}")
    return CalendarEvent(
        start=pkg.expected_date,
        end=(pkg.expected_date_end or pkg.expected_date) + ONE_DAY,
        summary=f"{prefix}: {name}",
        description="\n".join(details),
        uid=pkg.id,
    )


class DeliveriesCalendar(PackageTrackerEntity, CalendarEntity):
    """All expected deliveries as all-day events."""

    def _events(self) -> list[CalendarEvent]:
        events = [e for p in self.manager.packages if (e := _event(p)) is not None]
        events.sort(key=lambda e: (e.start, e.summary))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        """Current or next upcoming delivery that has not arrived yet."""
        today = self.manager.today
        upcoming = [
            event
            for pkg in self.manager.packages
            if pkg.is_active and (event := _event(pkg)) is not None and event.end > today
        ]
        return min(upcoming, key=lambda e: (e.start, e.summary), default=None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events overlapping ``[start_date, end_date)``."""
        start: date = dt_util.as_local(start_date).date()
        end: date = dt_util.as_local(end_date).date()
        # An all-day event covers [event.start, event.end); a range that ends at local
        # midnight does not include that day, one that ends later in the day does.
        if dt_util.as_local(end_date).time() != datetime.min.time():
            end += ONE_DAY
        return [e for e in self._events() if e.start < end and e.end > start]
