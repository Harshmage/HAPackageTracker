"""Serve the bundled Lovelace card and register it with the frontend.

Named ``card_registration.py`` (not ``frontend.py``) to avoid confusion with the
``frontend`` integration. The ``www/`` directory is served under a path containing a
short hash of its contents, so any change to any file (including the vendored lit module
that the card imports relatively) produces new URLs and browsers never run a stale mix.
The card is added with ``add_extra_js_url``, so users need no manual resource step.
"""

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.hass_dict import HassKey

from .const import CARD_FILENAME, DOMAIN, STATIC_URL_BASE

_LOGGER = logging.getLogger(__name__)

WWW_DIR = Path(__file__).parent / "www"


@dataclass(slots=True)
class CardRegistration:
    """Where the card is served and whether it is currently in the frontend's list."""

    url: str
    added: bool = False


DATA_CARD: HassKey[CardRegistration] = HassKey(f"{DOMAIN}_card")


def hash_directory(directory: Path) -> str:
    """First 8 hex chars of a sha256 over sorted relative paths and file bytes.

    Blocking I/O: run it in the executor.
    """
    digest = hashlib.sha256()
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    for path in files:
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:8]


async def async_register_card(hass: HomeAssistant) -> str:
    """Serve ``www/`` and add the card module to the frontend; returns the card URL."""
    key = await hass.async_add_executor_job(hash_directory, WWW_DIR)
    base = f"{STATIC_URL_BASE}/{key}"
    registration = CardRegistration(url=f"{base}/{CARD_FILENAME}")
    hass.data[DATA_CARD] = registration
    # hass.http is None only when http is not set up (some tests); the URL still exists.
    if hass.http is not None:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(base, str(WWW_DIR), cache_headers=True)]
        )
    async_add_card(hass)
    return registration.url


@callback
def async_add_card(hass: HomeAssistant) -> None:
    """Add the card URL to the frontend (idempotent; re-run when an entry is set up)."""
    registration = hass.data.get(DATA_CARD)
    if registration is None or registration.added:
        return
    if "frontend" not in hass.config.components:
        return
    add_extra_js_url(hass, registration.url)
    registration.added = True


@callback
def async_remove_card(hass: HomeAssistant) -> None:
    """Remove the card URL from the frontend (the static path stays until restart)."""
    registration = hass.data.get(DATA_CARD)
    if registration is None or not registration.added:
        return
    try:
        remove_extra_js_url(hass, registration.url)
    except KeyError:
        _LOGGER.debug("Card URL %s was not registered", registration.url)
    registration.added = False
