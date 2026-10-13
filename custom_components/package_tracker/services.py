"""Service actions (registered once in ``async_setup``).

Handlers resolve the manager on every call, so they keep working across entry reloads
and raise a translated ``not_loaded`` error when there is no loaded entry. All
validation errors are ``ServiceValidationError`` with translation keys, so the UI and
the card can show them in the user's language.
"""

from functools import partial
from typing import Any

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .carriers import CARRIERS, extract
from .const import (
    ATTR_ARRIVED,
    ATTR_CARRIER,
    ATTR_DESCRIPTION,
    ATTR_EXPECTED_DATE,
    ATTR_EXPECTED_DATE_END,
    ATTR_NOTES,
    ATTR_PACKAGE_ID,
    ATTR_TEXT,
    ATTR_TRACKING_NUMBER,
    ATTR_TRACKING_URL,
    ATTR_VENDOR,
    CARRIER_AUTO,
    DOMAIN,
    MAX_EXTRACT_TEXT,
    SERVICE_ADD_PACKAGE,
    SERVICE_ARCHIVE_PACKAGE,
    SERVICE_EXTRACT,
    SERVICE_MARK_ARRIVED,
    SERVICE_REMOVE_PACKAGE,
    SERVICE_RESTORE_PACKAGE,
    SERVICE_UPDATE_PACKAGE,
)
from .helpers import _get_manager, get_manager_or_none
from .manager import PackageManager
from .models import Package

CARRIER_CHOICES = [CARRIER_AUTO, *CARRIERS]
_TARGET = "target"

_TARGET_FIELDS: dict[Any, Any] = {
    vol.Exclusive(ATTR_PACKAGE_ID, _TARGET): cv.string,
    vol.Exclusive(ATTR_TRACKING_NUMBER, _TARGET): cv.string,
}


def _target_schema(extra: dict[Any, Any] | None = None) -> vol.All:
    return vol.All(
        vol.Schema({**_TARGET_FIELDS, **(extra or {})}),
        cv.has_at_least_one_key(ATTR_PACKAGE_ID, ATTR_TRACKING_NUMBER),
    )


ADD_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TRACKING_NUMBER): cv.string,
        vol.Optional(ATTR_CARRIER, default=CARRIER_AUTO): vol.In(CARRIER_CHOICES),
        vol.Optional(ATTR_DESCRIPTION): cv.string,
        vol.Optional(ATTR_VENDOR): cv.string,
        vol.Optional(ATTR_NOTES): cv.string,
        vol.Optional(ATTR_EXPECTED_DATE): cv.date,
        vol.Optional(ATTR_EXPECTED_DATE_END): cv.date,
        vol.Optional(ATTR_TRACKING_URL): cv.url,
    }
)

# update_package may change the number itself, so its two target fields are not
# exclusive: with package_id present, tracking_number is the *new* number; on its own it
# selects the package. An explicit null clears a field.
UPDATE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_PACKAGE_ID): cv.string,
            vol.Optional(ATTR_TRACKING_NUMBER): cv.string,
            vol.Optional(ATTR_CARRIER): vol.Any(None, vol.In(CARRIER_CHOICES)),
            vol.Optional(ATTR_DESCRIPTION): vol.Any(None, cv.string),
            vol.Optional(ATTR_VENDOR): vol.Any(None, cv.string),
            vol.Optional(ATTR_NOTES): vol.Any(None, cv.string),
            vol.Optional(ATTR_EXPECTED_DATE): vol.Any(None, cv.date),
            vol.Optional(ATTR_EXPECTED_DATE_END): vol.Any(None, cv.date),
            vol.Optional(ATTR_TRACKING_URL): vol.Any(None, cv.url),
        }
    ),
    cv.has_at_least_one_key(ATTR_PACKAGE_ID, ATTR_TRACKING_NUMBER),
)
ARRIVED_SCHEMA = _target_schema({vol.Optional(ATTR_ARRIVED, default=True): cv.boolean})
TARGET_SCHEMA = _target_schema()
EXTRACT_SCHEMA = vol.Schema({vol.Required(ATTR_TEXT): cv.string})


def _resolve(manager: PackageManager, call: ServiceCall) -> Package:
    if ATTR_PACKAGE_ID in call.data:
        return manager.get(package_id=call.data[ATTR_PACKAGE_ID])
    return manager.get(tracking_number=call.data[ATTR_TRACKING_NUMBER])


async def _async_add(call: ServiceCall) -> ServiceResponse:
    manager = _get_manager(call.hass)
    data = call.data
    pkg, candidates = await manager.async_add(
        tracking_number=data[ATTR_TRACKING_NUMBER],
        carrier=data.get(ATTR_CARRIER, CARRIER_AUTO),
        description=data.get(ATTR_DESCRIPTION),
        vendor=data.get(ATTR_VENDOR),
        notes=data.get(ATTR_NOTES),
        expected_date=data.get(ATTR_EXPECTED_DATE),
        expected_date_end=data.get(ATTR_EXPECTED_DATE_END),
        tracking_url=data.get(ATTR_TRACKING_URL),
        added_by=call.context.user_id,
        context=call.context,
    )
    if not call.return_response:
        return None
    return {"package": manager.api(pkg), "candidates": [c.as_dict() for c in candidates]}


async def _async_update(call: ServiceCall) -> ServiceResponse:
    manager = _get_manager(call.hass)
    by_id = ATTR_PACKAGE_ID in call.data
    pkg = _resolve(manager, call)
    changes = {
        key: value
        for key, value in call.data.items()
        if key != ATTR_PACKAGE_ID and (by_id or key != ATTR_TRACKING_NUMBER)
    }
    await manager.async_update(pkg, changes, context=call.context)
    if not call.return_response:
        return None
    return {"package": manager.api(pkg)}


async def _async_mark_arrived(call: ServiceCall) -> None:
    manager = _get_manager(call.hass)
    await manager.async_set_arrived(
        _resolve(manager, call), call.data[ATTR_ARRIVED], context=call.context
    )


async def _async_archive(call: ServiceCall) -> None:
    manager = _get_manager(call.hass)
    await manager.async_archive(_resolve(manager, call), context=call.context)


async def _async_restore(call: ServiceCall) -> None:
    manager = _get_manager(call.hass)
    await manager.async_restore(_resolve(manager, call), context=call.context)


async def _async_remove(call: ServiceCall) -> None:
    manager = _get_manager(call.hass)
    await manager.async_remove(_resolve(manager, call), context=call.context)


async def async_extract_results(hass: HomeAssistant, text: str) -> list[dict[str, Any]]:
    """Run extraction off the event loop and decorate results (shared with websocket)."""
    results = await hass.async_add_executor_job(
        partial(extract, text[:MAX_EXTRACT_TEXT], home_country=hass.config.country)
    )
    manager = get_manager_or_none(hass)
    tracked = manager.tracked_map() if manager else {}
    fallback = manager.fallback if manager else "none"
    return [r.as_dict(tracked=tracked, fallback=fallback) for r in results]


async def _async_extract(call: ServiceCall) -> ServiceResponse:
    # Works without a loaded entry: extraction is pure; only "already_tracked" needs it.
    return {"results": await async_extract_results(call.hass, call.data[ATTR_TEXT])}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register every service action for the domain."""
    register = hass.services.async_register
    register(
        DOMAIN,
        SERVICE_ADD_PACKAGE,
        _async_add,
        schema=ADD_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    register(
        DOMAIN,
        SERVICE_UPDATE_PACKAGE,
        _async_update,
        schema=UPDATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    register(DOMAIN, SERVICE_MARK_ARRIVED, _async_mark_arrived, schema=ARRIVED_SCHEMA)
    register(DOMAIN, SERVICE_ARCHIVE_PACKAGE, _async_archive, schema=TARGET_SCHEMA)
    register(DOMAIN, SERVICE_RESTORE_PACKAGE, _async_restore, schema=TARGET_SCHEMA)
    register(DOMAIN, SERVICE_REMOVE_PACKAGE, _async_remove, schema=TARGET_SCHEMA)
    register(
        DOMAIN,
        SERVICE_EXTRACT,
        _async_extract,
        schema=EXTRACT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
