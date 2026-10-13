"""Shared base for Package Tracker entities.

All entities hang off one service device (there are no per-package entities) and are
push-updated from the manager's listener list, so nothing polls.
"""

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .manager import PackageManager, PackageTrackerConfigEntry


class PackageTrackerEntity(Entity):
    """Base entity bound to the manager of one config entry."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: PackageTrackerConfigEntry, key: str) -> None:
        """Set ids, translation key and the service device."""
        self.manager: PackageManager = entry.runtime_data
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
            name=entry.title,
        )

    async def async_added_to_hass(self) -> None:
        """Follow manager changes for as long as the entity exists."""
        await super().async_added_to_hass()
        self.async_on_remove(self.manager.async_add_listener(self._handle_manager_update))

    @callback
    def _handle_manager_update(self) -> None:
        self.async_write_ha_state()
