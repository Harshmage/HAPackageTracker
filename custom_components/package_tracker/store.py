"""Persistence for packages via Home Assistant's JSON ``Store``.

Version 1.1 with no migration override yet: ``Package.from_dict`` tolerates unknown and
missing keys, so adding a field only needs a minor-version bump. The whole list is saved
after every mutation; it is small (tens to hundreds of records) and correctness after a
crash matters more than write volume.
"""

import logging
from typing import Any, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_MINOR_VERSION, STORAGE_VERSION
from .models import Package

_LOGGER = logging.getLogger(__name__)


class StoreData(TypedDict):
    """On-disk payload."""

    packages: list[dict[str, Any]]
    meta: dict[str, Any]


def _make_store(hass: HomeAssistant) -> Store[StoreData]:
    return Store[StoreData](hass, STORAGE_VERSION, STORAGE_KEY, minor_version=STORAGE_MINOR_VERSION)


class PackageStore:
    """Load and save the package list plus a small ``meta`` dict."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Create the underlying HA store."""
        self._store = _make_store(hass)

    async def async_load(self) -> tuple[list[Package], dict[str, Any]]:
        """Return ``(packages, meta)``; empty when nothing is stored yet."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return [], {}
        packages: list[Package] = []
        seen: set[str] = set()
        for raw in data.get("packages") or []:
            pkg = Package.from_dict(raw)
            if pkg is None or pkg.id in seen:
                _LOGGER.warning("Skipping unreadable stored package: %s", raw)
                continue
            seen.add(pkg.id)
            packages.append(pkg)
        meta = data.get("meta")
        return packages, dict(meta) if isinstance(meta, dict) else {}

    async def async_save(self, packages: list[Package], meta: dict[str, Any]) -> None:
        """Persist everything now."""
        await self._store.async_save(
            {"packages": [p.to_dict() for p in packages], "meta": dict(meta)}
        )


async def async_remove_store(hass: HomeAssistant) -> None:
    """Delete the store file (used when the config entry is removed)."""
    await _make_store(hass).async_remove()
