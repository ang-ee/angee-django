"""Settings owned by the Angee model and permission foundation."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from django.core.exceptions import ImproperlyConfigured
from django_compose.components import Closure
from django_compose.snapshot import discover_transitions

from angee.base.addons import capability_facts, package_document
from angee.base.paths import resolve_path

_Settings = TypedDict(
    "_Settings",
    {
        "COMPOSE_INTEGRATIONS:append": list[str],
        "COMPOSE_FINGERPRINT_GLOBS:append": list[str],
        "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": list[str],
        "DEBUG": bool,
        "USE_TZ": bool,
        "DEFAULT_AUTO_FIELD": str,
        "STATIC_URL": str,
        "MEDIA_URL": str,
        "ANGEE_REBAC_FORCE_OVERWRITE": bool,
        "ANGEE_MERGE_GUARDS": list[str],
        "REBAC_BACKEND": str,
        "REBAC_LOCAL_BACKEND_STORAGE": str,
        "REBAC_STRICT_MODE": bool,
        "REBAC_LINT_BARE_PREFETCH": bool,
        "REBAC_FIELD_READ_MODE": str,
        "REBAC_ALLOW_SUDO": bool,
        "REBAC_SUPERUSER_BYPASS": bool,
    },
)

SETTINGS: _Settings = {
    "COMPOSE_INTEGRATIONS:append": ["angee.base.build.PermissionIntegration"],
    "COMPOSE_FINGERPRINT_GLOBS:append": ["pyproject.toml", "**/*.zed"],
    "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": ["permissions"],
    "DEBUG": False,
    "USE_TZ": True,
    "DEFAULT_AUTO_FIELD": "django.db.models.BigAutoField",
    "STATIC_URL": "/static/",
    "MEDIA_URL": "/media/",
    "ANGEE_REBAC_FORCE_OVERWRITE": False,
    # Additive guards refuse removal before one record is folded into another.
    "ANGEE_MERGE_GUARDS": [],
    "REBAC_BACKEND": "local",
    "REBAC_LOCAL_BACKEND_STORAGE": "registry",
    "REBAC_STRICT_MODE": True,
    "REBAC_LINT_BARE_PREFETCH": False,
    "REBAC_FIELD_READ_MODE": "redact",
    "REBAC_ALLOW_SUDO": True,
    # Administrator reach is expressed in schema relations, so every actor uses grants.
    "REBAC_SUPERUSER_BYPASS": False,
}


class _DerivedSettings(TypedDict):
    REBAC_DEPTH_LIMIT: int
    ALLOWED_HOSTS: list[str]
    ANGEE_DATA_DIR: Path
    STATIC_ROOT: Path
    MEDIA_ROOT: Path
    DATABASES: dict[str, Any]
    COMPOSE_TRANSITION_SOURCE: NotRequired[tuple[Mapping[str, str], ...]]


def settings(namespace: Mapping[str, Any]) -> _DerivedSettings:
    """Derive storage/permission defaults and addon-owned transition facts.

    Explicit settings remain authoritative. Baseline declarations annotate native
    discovery from the already resolved closure; the compiler owns their meaning.
    """

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
    engine = databases.get("default", {}).get("ENGINE")
    derived: _DerivedSettings = {
        "ALLOWED_HOSTS": namespace.get("ALLOWED_HOSTS", ["*"] if namespace.get("DEBUG", False) else []),
        "ANGEE_DATA_DIR": data,
        "STATIC_ROOT": resolve_path(namespace.get("STATIC_ROOT", data / "static")),
        "MEDIA_ROOT": resolve_path(namespace.get("MEDIA_ROOT", data / "media")),
        "DATABASES": databases,
        "REBAC_DEPTH_LIMIT": int(namespace.get(
            "REBAC_DEPTH_LIMIT", 8 if engine == "django.db.backends.sqlite3" else 16,
        )),
    }
    if "COMPOSE_TRANSITION_SOURCE" not in namespace:
        closure: Closure = namespace["COMPOSE_CLOSURE"]
        records = tuple(dict(record) for record in discover_transitions(closure.app_entries))
        by_origin = {(record["declaring_app"], record["name"]): record for record in records}
        for distribution_name, app_name in sorted(closure.components.items()):
            declarations = capability_facts(
                package_document(metadata.distribution(distribution_name), app_name),
                "angee.migrations.initial_baseline",
            )
            for name, value in declarations.items():
                record = by_origin.get((app_name, name))
                if record is None:
                    raise ImproperlyConfigured(
                        f"{app_name}: initial_baseline names unknown transition {name!r}"
                    )
                # The compiler validates the declaration and owns baseline semantics.
                record["initial_baseline"] = value
        derived["COMPOSE_TRANSITION_SOURCE"] = records
    return derived
