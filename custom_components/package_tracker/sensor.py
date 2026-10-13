"""Count sensors plus the next expected delivery date.

The ``packages`` attribute is capped and excluded from the recorder: it changes often,
can be large, and the history of who-ordered-what is not useful long-term data.
"""

from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_SENSOR_PACKAGES
from .entity import PackageTrackerEntity
from .manager import PackageTrackerConfigEntry
from .models import Package, Status, compute_status

PARALLEL_UPDATES = 0

ATTR_PACKAGES = "packages"

_COUNT_STATUSES: dict[str, frozenset[Status]] = {
    "active": frozenset({Status.NO_DATE, Status.IN_TRANSIT, Status.DUE_TODAY, Status.OVERDUE}),
    "overdue": frozenset({Status.OVERDUE}),
    "due_today": frozenset({Status.DUE_TODAY}),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PackageTrackerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the sensors."""
    async_add_entities(
        [
            *(PackageCountSensor(entry, key) for key in _COUNT_STATUSES),
            NextDeliverySensor(entry, "next_delivery"),
        ]
    )


def _summary(pkg: Package, status: Status) -> dict[str, Any]:
    return {
        "id": pkg.id,
        "tracking_number": pkg.tracking_number,
        "carrier": pkg.carrier,
        "description": pkg.description,
        "vendor": pkg.vendor,
        "expected_date": pkg.expected_date.isoformat() if pkg.expected_date else None,
        "expected_date_end": pkg.expected_date_end.isoformat() if pkg.expected_date_end else None,
        "status": str(status),
    }


def _sort_key(pkg: Package) -> tuple[date, date]:
    return (pkg.expected_date or date.max, pkg.created_at.date())


class PackageCountSensor(PackageTrackerEntity, SensorEntity):
    """Number of packages in a set of statuses."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({ATTR_PACKAGES})

    def _matching(self) -> list[tuple[Package, Status]]:
        today = self.manager.today
        wanted = _COUNT_STATUSES[self._attr_translation_key or ""]
        matches = [(p, compute_status(p, today)) for p in self.manager.packages]
        matches = [(p, s) for p, s in matches if s in wanted]
        matches.sort(key=lambda item: _sort_key(item[0]))
        return matches

    @property
    def native_value(self) -> int:
        """Package count."""
        return len(self._matching())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The first packages (capped) for templates and dashboards."""
        return {ATTR_PACKAGES: [_summary(p, s) for p, s in self._matching()[:MAX_SENSOR_PACKAGES]]}


class NextDeliverySensor(PackageTrackerEntity, SensorEntity):
    """Earliest upcoming expected delivery date among active packages."""

    _attr_device_class = SensorDeviceClass.DATE
    _unrecorded_attributes = frozenset({ATTR_PACKAGES})

    def _next(self) -> tuple[date | None, list[tuple[Package, Status]]]:
        today = self.manager.today
        upcoming: dict[date, list[tuple[Package, Status]]] = {}
        for pkg in self.manager.packages:
            status = compute_status(pkg, today)
            if status is Status.DUE_TODAY:
                # A delivery window that started earlier still means "today".
                upcoming.setdefault(today, []).append((pkg, status))
            elif status is Status.IN_TRANSIT and pkg.expected_date is not None:
                upcoming.setdefault(pkg.expected_date, []).append((pkg, status))
        if not upcoming:
            return None, []
        first = min(upcoming)
        return first, sorted(upcoming[first], key=lambda item: _sort_key(item[0]))

    @property
    def native_value(self) -> date | None:
        """Next delivery date, or unknown when nothing is expected."""
        return self._next()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Packages expected on that date."""
        return {ATTR_PACKAGES: [_summary(p, s) for p, s in self._next()[1][:MAX_SENSOR_PACKAGES]]}
