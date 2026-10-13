"""Diagnostics with personal data redacted.

Tracking numbers, notes, override links and user ids identify people and orders, so
they are removed; statuses, dates and carriers are what debugging actually needs.
"""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .manager import PackageTrackerConfigEntry

TO_REDACT = {"notes", "tracking_url_override", "tracking_number", "added_by"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PackageTrackerConfigEntry
) -> dict[str, Any]:
    """Return redacted options, store meta and packages with derived status."""
    manager = entry.runtime_data
    today = manager.today
    packages = []
    for pkg in manager.packages:
        data = pkg.to_dict()
        data["status"] = str(manager.status(pkg, today))
        packages.append(data)
    return {
        "options": dict(entry.options),
        "today": today.isoformat(),
        "meta": dict(manager.meta),
        "counts": manager.counts(today),
        "packages": async_redact_data(packages, TO_REDACT),
    }
