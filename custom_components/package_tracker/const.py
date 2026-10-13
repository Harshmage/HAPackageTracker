"""Constants shared across the Package Tracker integration.

Kept free of runtime logic so every module (and the tests) can import it without
pulling in Home Assistant machinery or creating import cycles.
"""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "package_tracker"

# Persistence -----------------------------------------------------------------
STORAGE_KEY: Final = f"{DOMAIN}.packages"
STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1

# Frontend --------------------------------------------------------------------
# The cache key (hash of the www/ directory) is inserted between the base path and
# the file name so that every changed file, including relative ES-module imports,
# gets a fresh URL. See card_registration.py.
STATIC_URL_BASE: Final = f"/{DOMAIN}_static"
CARD_FILENAME: Final = "package-tracker-card.js"
CARD_ELEMENT: Final = "package-tracker-card"

# Options (config entry options) ----------------------------------------------
CONF_NOTIFY_TIME: Final = "notify_time"
CONF_NOTIFY_ON: Final = "notify_on"
CONF_NOTIFY_ENTITIES: Final = "notify_entities"
CONF_NOTIFY_SERVICES: Final = "notify_services"
CONF_PERSISTENT_NOTIFICATION: Final = "persistent_notification"
CONF_REMIND_OVERDUE_DAILY: Final = "remind_overdue_daily"
CONF_ARCHIVE_AFTER_DAYS: Final = "archive_after_days"
CONF_PURGE_AFTER_DAYS: Final = "purge_after_days"
CONF_FALLBACK_TRACKER: Final = "fallback_tracker"

NOTIFY_DUE_TODAY: Final = "due_today"
NOTIFY_OVERDUE: Final = "overdue"
NOTIFY_ARRIVED: Final = "arrived"
NOTIFY_KINDS: Final = (NOTIFY_DUE_TODAY, NOTIFY_OVERDUE, NOTIFY_ARRIVED)

FALLBACK_NONE: Final = "none"
FALLBACK_17TRACK: Final = "17track"
FALLBACK_PARCELSAPP: Final = "parcelsapp"
FALLBACK_TRACKERS: Final = (FALLBACK_NONE, FALLBACK_17TRACK, FALLBACK_PARCELSAPP)

DEFAULT_NOTIFY_TIME: Final = "08:00:00"
DEFAULT_NOTIFY_ON: Final = [NOTIFY_DUE_TODAY, NOTIFY_OVERDUE]
DEFAULT_PERSISTENT_NOTIFICATION: Final = True
DEFAULT_REMIND_OVERDUE_DAILY: Final = False
DEFAULT_ARCHIVE_AFTER_DAYS: Final = 7
DEFAULT_PURGE_AFTER_DAYS: Final = 0  # 0 = keep history forever
DEFAULT_FALLBACK_TRACKER: Final = FALLBACK_17TRACK

# Daily housekeeping runs a few seconds after local midnight so "today" has rolled over.
MIDNIGHT_TICK: Final = {"hour": 0, "minute": 0, "second": 5}

# Services --------------------------------------------------------------------
SERVICE_ADD_PACKAGE: Final = "add_package"
SERVICE_UPDATE_PACKAGE: Final = "update_package"
SERVICE_MARK_ARRIVED: Final = "mark_arrived"
SERVICE_ARCHIVE_PACKAGE: Final = "archive_package"
SERVICE_RESTORE_PACKAGE: Final = "restore_package"
SERVICE_REMOVE_PACKAGE: Final = "remove_package"
SERVICE_EXTRACT: Final = "extract_tracking_numbers"

ATTR_PACKAGE_ID: Final = "package_id"
ATTR_TRACKING_NUMBER: Final = "tracking_number"
ATTR_CARRIER: Final = "carrier"
ATTR_DESCRIPTION: Final = "description"
ATTR_VENDOR: Final = "vendor"
ATTR_NOTES: Final = "notes"
ATTR_EXPECTED_DATE: Final = "expected_date"
ATTR_EXPECTED_DATE_END: Final = "expected_date_end"
ATTR_TRACKING_URL: Final = "tracking_url"
ATTR_ARRIVED: Final = "arrived"
ATTR_TEXT: Final = "text"

CARRIER_AUTO: Final = "auto"
CARRIER_UNKNOWN: Final = "unknown"

MAX_EXTRACT_TEXT: Final = 100_000
MAX_EXTRACT_RESULTS: Final = 50
MAX_SENSOR_PACKAGES: Final = 25

# Bus events ------------------------------------------------------------------
EVENT_ADDED: Final = f"{DOMAIN}_added"
EVENT_DUE_TODAY: Final = f"{DOMAIN}_due_today"
EVENT_OVERDUE: Final = f"{DOMAIN}_overdue"
EVENT_ARRIVED: Final = f"{DOMAIN}_arrived"
EVENT_ARCHIVED: Final = f"{DOMAIN}_archived"
EVENT_REMOVED: Final = f"{DOMAIN}_removed"
ALL_EVENTS: Final = (
    EVENT_ADDED,
    EVENT_DUE_TODAY,
    EVENT_OVERDUE,
    EVENT_ARRIVED,
    EVENT_ARCHIVED,
    EVENT_REMOVED,
)

# Translation keys for ServiceValidationError --------------------------------
ERR_NOT_LOADED: Final = "not_loaded"
ERR_PACKAGE_NOT_FOUND: Final = "package_not_found"
ERR_DUPLICATE_PACKAGE: Final = "duplicate_package"
ERR_IN_HISTORY: Final = "in_history"
ERR_INVALID_DATE_RANGE: Final = "invalid_date_range"
ERR_INVALID_TRACKING_NUMBER: Final = "invalid_tracking_number"

DIGEST_NOTIFICATION_ID: Final = f"{DOMAIN}_digest"
ARRIVED_NOTIFICATION_ID: Final = f"{DOMAIN}_arrived"

ONE_DAY: Final = timedelta(days=1)
