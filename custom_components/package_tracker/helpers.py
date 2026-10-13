"""Small shared helpers: the update signal and manager lookup.

Services and websocket commands are registered once in ``async_setup`` and outlive any
single config entry, so they must look the manager up on every call instead of
capturing it. ``async_loaded_entries`` only returns an entry once it is fully LOADED,
which is exactly when the manager is safe to use.
"""

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util.signal_type import SignalType

from .const import DOMAIN, ERR_NOT_LOADED

if TYPE_CHECKING:
    from .manager import PackageManager

# Fired (no payload) whenever the package list or the entry state changes; websocket
# subscribers re-read a full snapshot.
SIGNAL_UPDATED = SignalType[()](f"{DOMAIN}_updated")


def get_manager_or_none(hass: HomeAssistant) -> PackageManager | None:
    """Return the loaded entry's manager, or ``None`` when not loaded."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        return None
    return entries[0].runtime_data


def _get_manager(hass: HomeAssistant) -> PackageManager:
    """Return the loaded entry's manager or raise a translated ``not_loaded`` error."""
    if (manager := get_manager_or_none(hass)) is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key=ERR_NOT_LOADED)
    return manager
