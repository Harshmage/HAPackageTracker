"""Package Tracker: a Home Assistant integration for tracking parcels without carrier APIs.

``async_setup`` registers everything that must exist even without a config entry
(services, websocket commands, the card). The config entry owns the runtime state: the
``PackageManager`` lives in ``entry.runtime_data`` and every timer or listener is tied to
the entry via ``async_on_unload``.

Two ordering rules matter here:

* Transition events and the missed-digest catch-up wait for ``async_at_started`` so
  automations and notify services that load after us do not miss them.
* Websocket subscribers are told about entry state changes through
  ``async_on_state_change``. There is no manual dispatch during setup or unload, because
  ``async_loaded_entries`` only sees the entry once it is LOADED; the state-change hook
  fires at exactly the right moments.
"""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .card_registration import async_add_card, async_register_card, async_remove_card
from .const import DOMAIN
from .helpers import SIGNAL_UPDATED
from .manager import PackageManager, PackageTrackerConfigEntry
from .scheduler import async_catch_up, async_setup_scheduler
from .services import async_setup_services
from .store import PackageStore, async_remove_store
from .websocket import async_setup_websocket

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CALENDAR]

__all__ = ["PackageTrackerConfigEntry"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services, websocket commands and the card (once per HA run)."""
    async_setup_services(hass)
    async_setup_websocket(hass)
    await async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PackageTrackerConfigEntry) -> bool:
    """Load packages, start entities and timers."""
    manager = PackageManager(hass, entry, PackageStore(hass))
    await manager.async_load()
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_setup_scheduler(hass, entry, manager)

    async def _async_start() -> None:
        await manager.async_start()
        await async_catch_up(manager)

    @callback
    def _started(_hass: HomeAssistant) -> None:
        # An entry task, so unloading waits for it instead of racing it.
        entry.async_create_task(hass, _async_start(), f"{DOMAIN} start")

    entry.async_on_unload(async_at_started(hass, _started))

    # The entry may have been removed and re-added without a restart.
    async_add_card(hass)

    entry.async_on_unload(
        entry.async_on_state_change(lambda: async_dispatcher_send(hass, SIGNAL_UPDATED))
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PackageTrackerConfigEntry) -> bool:
    """Unload platforms; timers and listeners are removed via ``async_on_unload``."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: PackageTrackerConfigEntry) -> None:
    """Delete stored packages and take the card out of the frontend."""
    await async_remove_store(hass)
    async_remove_card(hass)
