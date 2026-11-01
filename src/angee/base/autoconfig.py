"""Settings owned by the permission-neutral Angee foundation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

from angee.base.paths import resolve_path

_Settings = TypedDict(
    "_Settings",
    {
        "DEBUG": bool,
        "USE_TZ": bool,
        "DEFAULT_AUTO_FIELD": str,
        "STATIC_URL": str,
        "MEDIA_URL": str,
        "ANGEE_MERGE_GUARDS": list[str],
    },
)

SETTINGS: _Settings = {
    "DEBUG": False,
    "USE_TZ": True,
    "DEFAULT_AUTO_FIELD": "django.db.models.BigAutoField",
    "STATIC_URL": "/static/",
    "MEDIA_URL": "/media/",
    # Additive guards refuse removal before one record is folded into another.
    "ANGEE_MERGE_GUARDS": [],
}


class _DerivedSettings(TypedDict):
    ALLOWED_HOSTS: list[str]
    ANGEE_DATA_DIR: Path
    STATIC_ROOT: Path
    MEDIA_ROOT: Path
    DATABASES: dict[str, Any]


def settings(namespace: Mapping[str, Any]) -> _DerivedSettings:
    """Derive storage defaults while preserving project settings."""

    data = resolve_path(namespace.get("ANGEE_DATA_DIR", resolve_path(namespace["BASE_DIR"]) / ".angee" / "data"))
    databases = namespace.get("DATABASES", {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": data / "db.sqlite3",
            "OPTIONS": {
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
                "transaction_mode": "IMMEDIATE",
                "timeout": 20,
            },
        },
    })
    derived: _DerivedSettings = {
        "ALLOWED_HOSTS": namespace.get("ALLOWED_HOSTS", ["*"] if namespace.get("DEBUG", False) else []),
        "ANGEE_DATA_DIR": data,
        "STATIC_ROOT": resolve_path(namespace.get("STATIC_ROOT", data / "static")),
        "MEDIA_ROOT": resolve_path(namespace.get("MEDIA_ROOT", data / "media")),
        "DATABASES": databases,
    }
    return derived
