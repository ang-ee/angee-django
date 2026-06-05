"""Default Django settings for Angee-composed hosts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured


def _path_value(value: Any) -> Path:
    """Return ``value`` as an absolute path."""

    return Path(value).expanduser().resolve()


def _sequence(value: Any) -> tuple[Any, ...]:
    """Return ``value`` as a tuple, treating strings as one item."""

    if value is None:
        return ()
    if isinstance(value, str | os.PathLike):
        return (value,)
    return tuple(value)


_BASE_DIR = globals().get("BASE_DIR")
if _BASE_DIR is None:
    raise ImproperlyConfigured("angee.compose.defaults must be evaluated by angee.compose.settings")

BASE_DIR = _path_value(_BASE_DIR)
DEBUG = bool(globals().get("DEBUG", False))
ALLOWED_HOSTS = globals().get("ALLOWED_HOSTS", ["*"] if DEBUG else [])
USE_TZ = globals().get("USE_TZ", True)
DEFAULT_AUTO_FIELD = globals().get("DEFAULT_AUTO_FIELD", "django.db.models.BigAutoField")

_configured_installed_apps = globals().get("INSTALLED_APPS", ())
if isinstance(_configured_installed_apps, str):
    _configured_installed_apps = (_configured_installed_apps,)
INSTALLED_APPS = [
    "angee.compose",
    *(entry for entry in _configured_installed_apps if entry != "angee.compose"),
]

ANGEE_RUNTIME_MODULE = globals().get("ANGEE_RUNTIME_MODULE", "runtime")
ANGEE_DATA_DIR = _path_value(globals().get("ANGEE_DATA_DIR", BASE_DIR / ".angee" / "data"))
ANGEE_ADDON_DIRS = tuple(
    _path_value(path) for path in _sequence(globals().get("ANGEE_ADDON_DIRS", (BASE_DIR / "addons",)))
)

STATIC_URL = globals().get("STATIC_URL", "/static/")
STATIC_ROOT = _path_value(globals().get("STATIC_ROOT", ANGEE_DATA_DIR / "static"))
MEDIA_URL = globals().get("MEDIA_URL", "/media/")
MEDIA_ROOT = _path_value(globals().get("MEDIA_ROOT", ANGEE_DATA_DIR / "media"))

DATABASES = globals().get(
    "DATABASES",
    {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ANGEE_DATA_DIR / "db.sqlite3",
            "OPTIONS": {
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
                "transaction_mode": "IMMEDIATE",
                "timeout": 20,
            },
        }
    },
)
