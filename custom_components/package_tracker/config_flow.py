"""Config and options flows.

The config flow is confirm-only: there is nothing to configure up front, and
``single_config_entry`` in the manifest makes HA abort a second attempt for us. The
options flow uses ``OptionsFlowWithReload`` so saving options simply reloads the entry;
there are deliberately no update listeners.
"""

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TimeSelector,
)
import voluptuous as vol

from .const import (
    CONF_ARCHIVE_AFTER_DAYS,
    CONF_FALLBACK_TRACKER,
    CONF_NOTIFY_ENTITIES,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_SERVICES,
    CONF_NOTIFY_TIME,
    CONF_PERSISTENT_NOTIFICATION,
    CONF_PURGE_AFTER_DAYS,
    CONF_REMIND_OVERDUE_DAILY,
    DEFAULT_ARCHIVE_AFTER_DAYS,
    DEFAULT_FALLBACK_TRACKER,
    DEFAULT_NOTIFY_ON,
    DEFAULT_NOTIFY_TIME,
    DEFAULT_PERSISTENT_NOTIFICATION,
    DEFAULT_PURGE_AFTER_DAYS,
    DEFAULT_REMIND_OVERDUE_DAILY,
    DOMAIN,
    FALLBACK_TRACKERS,
    NOTIFY_KINDS,
)

TITLE = "Package Tracker"

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_NOTIFY_TIME: DEFAULT_NOTIFY_TIME,
    CONF_NOTIFY_ON: list(DEFAULT_NOTIFY_ON),
    CONF_NOTIFY_ENTITIES: [],
    CONF_NOTIFY_SERVICES: [],
    CONF_PERSISTENT_NOTIFICATION: DEFAULT_PERSISTENT_NOTIFICATION,
    CONF_REMIND_OVERDUE_DAILY: DEFAULT_REMIND_OVERDUE_DAILY,
    CONF_ARCHIVE_AFTER_DAYS: DEFAULT_ARCHIVE_AFTER_DAYS,
    CONF_PURGE_AFTER_DAYS: DEFAULT_PURGE_AFTER_DAYS,
    CONF_FALLBACK_TRACKER: DEFAULT_FALLBACK_TRACKER,
}

# Not legacy per-device services: send_message is the entity service (covered by the
# entity selector) and persistent_notification has its own toggle.
_EXCLUDED_NOTIFY_SERVICES = frozenset({"send_message", "persistent_notification"})

_DAYS = NumberSelector(NumberSelectorConfig(min=0, max=3650, step=1, mode=NumberSelectorMode.BOX))


class PackageTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Confirm-only setup."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for confirmation, then create the single entry."""
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={}, options=dict(DEFAULT_OPTIONS))
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PackageTrackerOptionsFlow:
        """Return the options flow."""
        return PackageTrackerOptionsFlow()


class PackageTrackerOptionsFlow(OptionsFlowWithReload):
    """Notification, housekeeping and link options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Single options page."""
        if user_input is not None:
            data = {**DEFAULT_OPTIONS, **user_input}
            # NumberSelector returns floats.
            data[CONF_ARCHIVE_AFTER_DAYS] = int(data[CONF_ARCHIVE_AFTER_DAYS])
            data[CONF_PURGE_AFTER_DAYS] = int(data[CONF_PURGE_AFTER_DAYS])
            return self.async_create_entry(data=data)

        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(self._schema(current), current),
        )

    def _schema(self, current: dict[str, Any]) -> vol.Schema:
        services = {
            name
            for name in self.hass.services.async_services_for_domain("notify")
            if name not in _EXCLUDED_NOTIFY_SERVICES
        }
        # Keep configured services listed even if their integration is not loaded now.
        services.update(current.get(CONF_NOTIFY_SERVICES) or [])
        return vol.Schema(
            {
                vol.Required(CONF_NOTIFY_TIME): TimeSelector(),
                vol.Optional(CONF_NOTIFY_ON, default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=list(NOTIFY_KINDS),
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_NOTIFY_ON,
                    )
                ),
                vol.Optional(CONF_NOTIFY_ENTITIES, default=[]): EntitySelector(
                    EntitySelectorConfig(domain="notify", multiple=True)
                ),
                vol.Optional(CONF_NOTIFY_SERVICES, default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=sorted(services),
                        multiple=True,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_PERSISTENT_NOTIFICATION): BooleanSelector(),
                vol.Required(CONF_REMIND_OVERDUE_DAILY): BooleanSelector(),
                vol.Required(CONF_ARCHIVE_AFTER_DAYS): _DAYS,
                vol.Required(CONF_PURGE_AFTER_DAYS): _DAYS,
                vol.Required(CONF_FALLBACK_TRACKER): SelectSelector(
                    SelectSelectorConfig(
                        options=list(FALLBACK_TRACKERS),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_FALLBACK_TRACKER,
                    )
                ),
            }
        )
