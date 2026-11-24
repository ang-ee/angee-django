"""Settings owned by the Angee model and permission foundation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import environ
from django.core.exceptions import ImproperlyConfigured

from angee.base.origins import public_origin
from angee.base.paths import resolve_path

_Settings = TypedDict(
    "_Settings",
    {
        "COMPOSE_INTEGRATIONS:append": list[str],
        "COMPOSE_FINGERPRINT_GLOBS:append": list[str],
        "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": list[str],
        "ANGEE_PROJECT_YAML_SETTINGS": frozenset[str],
        "DEBUG": bool,
        "USE_TZ": bool,
        "DEFAULT_AUTO_FIELD": str,
        "STATIC_URL": str,
        "MEDIA_URL": str,
        "ANGEE_REBAC_FORCE_OVERWRITE": bool,
        "ANGEE_MERGE_GUARDS": list[str],
        "ANGEE_IMPL_KEY_REMEDIES": dict[str, dict[str, str]],
        "REBAC_LOCAL_BACKEND_STORAGE": str,
        "REBAC_STRICT_MODE": bool,
        "REBAC_LINT_BARE_PREFETCH": bool,
        "REBAC_FIELD_READ_MODE": str,
        "REBAC_ALLOW_SUDO": bool,
        "REBAC_SUPERUSER_BYPASS": bool,
        "ANGEE_REBAC_READ_THROUGH_PARENT": bool,
        "ANGEE_PUBLIC_ORIGIN": str,
    },
)

SETTINGS: _Settings = {
    "COMPOSE_INTEGRATIONS:append": ["angee.base.build.PermissionIntegration"],
    "COMPOSE_FINGERPRINT_GLOBS:append": ["pyproject.toml", "**/*.zed"],
    "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": ["permissions"],
    "ANGEE_PROJECT_YAML_SETTINGS": frozenset(),
    "DEBUG": False,
    "USE_TZ": True,
    "DEFAULT_AUTO_FIELD": "django.db.models.BigAutoField",
    "STATIC_URL": "/static/",
    "MEDIA_URL": "/media/",
    "ANGEE_REBAC_FORCE_OVERWRITE": False,
    # Additive guards refuse removal before one record is folded into another; a guard's
    # reason names the record by public id, never by name (``angee.base.models.merge``).
    "ANGEE_MERGE_GUARDS": [],
    # What an operator does about stored rows whose implementation key nothing
    # registers (`angee.E052`), by registry setting and key. The addon that
    # stopped providing a key adds its line with a yamlconf dotted key, e.g.
    # ``"ANGEE_IMPL_KEY_REMEDIES.<REGISTRY_SETTING>.<key>"``.
    "ANGEE_IMPL_KEY_REMEDIES": {},
    "REBAC_LOCAL_BACKEND_STORAGE": "registry",
    "REBAC_STRICT_MODE": True,
    "REBAC_LINT_BARE_PREFETCH": False,
    "REBAC_FIELD_READ_MODE": "redact",
    "REBAC_ALLOW_SUDO": True,
    # Administrator reach is expressed in schema relations, so every actor uses grants.
    "REBAC_SUPERUSER_BYPASS": False,
    # Children read with their admitted parent rows and a referred record's label shown with
    # the row that refers to it, by REBAC's structural prefetch and display rows (`settings`
    # below): off, every relation a read loads keeps its permission statement.
    "ANGEE_REBAC_READ_THROUGH_PARENT": False,
    # The deployment's public browser origin (`https://host`, no path), fed by the
    # ANGEE_PUBLIC_ORIGIN environment variable the stack sets. A valid one joins
    # the CSRF trusted origins, and owners that build links without a request
    # read it through `angee.base.origins.public_origin`.
    "ANGEE_PUBLIC_ORIGIN": "",
}


_DerivedSettings = TypedDict(
    "_DerivedSettings",
    {
        "REBAC_DEPTH_LIMIT": int,
        "REBAC_STRUCTURAL_PREFETCH": bool,
        "REBAC_DISPLAY_FIELDS": NotRequired[bool | str],
        "ALLOWED_HOSTS": list[str],
        "ANGEE_DATA_DIR": Path,
        "STATIC_ROOT": Path,
        "MEDIA_ROOT": Path,
        "DATABASES": dict[str, Any],
        "CSRF_TRUSTED_ORIGINS:append": NotRequired[list[str]],
    },
)


def settings(namespace: Mapping[str, Any]) -> _DerivedSettings:
    """Derive storage and permission defaults while preserving project settings.

    The resolved ``ANGEE_PUBLIC_ORIGIN``, authored or from the environment,
    joins ``CSRF_TRUSTED_ORIGINS`` when it is a valid public origin
    (:func:`angee.base.origins.public_origin`); ``angee.E051`` reports one that
    is not.

    ``ANGEE_REBAC_READ_THROUGH_PARENT``, authored or from the environment (``true``,
    ``1``; :func:`read_through_parent`), turns on REBAC's
    ``REBAC_STRUCTURAL_PREFETCH`` and ``REBAC_DISPLAY_FIELDS``, each as the boolean
    ``True`` unless the project sets it itself; nothing else follows from it. REBAC 0.36.0
    to 0.37.0 deliver children their own statement withholds (below a parent
    admitted at a recursion's depth limit, on another database, under a recaveat
    override): never turn the switch on with them. A REBAC with those fixes alone
    delivers no child to the console's documents, which admit messages under
    ``read_inbox``; that needs one that proves one action implies another
    (``rebac.structural.implies``). With the switch on, ``REBAC_DISPLAY_FIELDS``
    set to ``"fallback"`` is refused, and so is a REBAC without display rows through
    a prefetch (``RebacQuerySet.for_display``) while ``REBAC_DISPLAY_FIELDS`` is on:
    both raise ``ImproperlyConfigured`` here. The backend guidelines' "Reading
    through the parent" says what is delivered.
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
    through_parent = read_through_parent(namespace.get("ANGEE_REBAC_READ_THROUGH_PARENT"))
    derived: _DerivedSettings = {
        "ALLOWED_HOSTS": namespace.get("ALLOWED_HOSTS", ["*"] if namespace.get("DEBUG", False) else []),
        "ANGEE_DATA_DIR": data,
        "STATIC_ROOT": resolve_path(namespace.get("STATIC_ROOT", data / "static")),
        "MEDIA_ROOT": resolve_path(namespace.get("MEDIA_ROOT", data / "media")),
        "DATABASES": databases,
        "REBAC_DEPTH_LIMIT": int(namespace.get(
            "REBAC_DEPTH_LIMIT", 8 if engine == "django.db.backends.sqlite3" else 16,
        )),
        "REBAC_STRUCTURAL_PREFETCH": namespace.get("REBAC_STRUCTURAL_PREFETCH", through_parent),
    }
    if through_parent:
        derived["REBAC_DISPLAY_FIELDS"] = _display_fields(namespace.get("REBAC_DISPLAY_FIELDS", True))
    origin = public_origin(namespace.get("ANGEE_PUBLIC_ORIGIN"), debug=bool(namespace.get("DEBUG", False)))
    if origin and origin not in namespace.get("CSRF_TRUSTED_ORIGINS", ()):
        derived["CSRF_TRUSTED_ORIGINS:append"] = [origin]
    return derived


def read_through_parent(value: object) -> bool:
    """``ANGEE_REBAC_READ_THROUGH_PARENT`` as the boolean it reads as.

    Unset or ``None`` reads as off, as ``DEBUG`` does; a string is the environment's
    spelling (``true``, ``1``), which the setting can hold as it is.
    """

    return bool(environ.Env.parse_value(value or False, bool))


def _display_fields(value: Any) -> bool | str:
    """``REBAC_DISPLAY_FIELDS`` under ``ANGEE_REBAC_READ_THROUGH_PARENT``, or ``ImproperlyConfigured``.

    ``"fallback"`` is refused: under it a row ``rebac_select_related`` joins, of a
    model with display fields, that the reader may not read is shown with them
    where the read raised before. On (``True`` or ``"always"``), the installed REBAC
    must deliver display rows through a prefetch, which the switch's relation fields
    ask for.
    """

    if value == "fallback":
        raise ImproperlyConfigured(
            'ANGEE_REBAC_READ_THROUGH_PARENT refuses REBAC_DISPLAY_FIELDS = "fallback". '
            "Leave it unset (the switch sets True) or set it to False."
        )
    if value is True or value == "always":
        from rebac.managers import RebacQuerySet

        if not hasattr(RebacQuerySet, "for_display"):
            raise ImproperlyConfigured(
                "ANGEE_REBAC_READ_THROUGH_PARENT shows labels through REBAC's display rows through a prefetch "
                "(RebacQuerySet.for_display), which the installed django-zed-rebac does not have. Install a "
                "release that has them, or set REBAC_DISPLAY_FIELDS = False to read children alone."
            )
    return value
