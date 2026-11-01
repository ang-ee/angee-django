"""Settings fragments required by Angee IAM."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from typing import Any

from django.conf import settings as django_settings

SETTINGS = {
    "ANGEE_GRAPHQL_SCHEMA_CLASS:append": ["angee.iam.graphql.schema.IAMSchema"],
    "ANGEE_GRAPHQL_HTTP_VIEW_CLASS:append": ["angee.iam.graphql.views.IAMGraphQLView"],
    "ANGEE_GRAPHQL_WS_CONSUMER_CLASS:append": ["angee.iam.graphql.consumers.IAMGraphQLWSConsumer"],
    "ANGEE_GRAPHQL_RESOURCE_CONTRIBUTORS:append": ["angee.iam.graphql.resources.contribute"],
    "ANGEE_GRAPHQL_RESOURCE_METADATA:append": ["angee.iam.graphql.resources.enrich_metadata"],
    "ANGEE_GRAPHQL_CHANGE_PROJECTORS:append": ["angee.iam.graphql.changes.project"],
    "ANGEE_GRAPHQL_CHANGE_FILTERS:append": ["angee.iam.graphql.changes.filter_event"],
    "COMPOSE_INTEGRATIONS:append": ["angee.iam.build.PermissionIntegration"],
    "COMPOSE_FINGERPRINT_GLOBS:append": ["pyproject.toml", "**/*.zed"],
    "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": ["permissions"],
    "ANGEE_REBAC_FORCE_OVERWRITE": False,
    "REBAC_BACKEND": "local",
    "REBAC_LOCAL_BACKEND_STORAGE": "registry",
    "REBAC_STRICT_MODE": True,
    "REBAC_LINT_BARE_PREFETCH": False,
    "REBAC_FIELD_READ_MODE": "redact",
    "REBAC_ALLOW_SUDO": True,
    "REBAC_SUPERUSER_BYPASS": False,
    "COMPOSE_COMMAND_CONTEXT:append": ["angee.iam.context.command_context"],
    "AUTH_USER_MODEL": "iam.User",
    # IAM owns the platform-admin role used by its schema and bootstrap command.
    "REBAC_UNIVERSAL_ADMIN_ROLE": "angee/role:admin",
    "MIDDLEWARE:append": [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "rebac.middleware.ActorMiddleware",
        "angee.iam.middleware.BearerTokenCsrfExemptMiddleware",
        "axes.middleware.AxesMiddleware",
    ],
    "AUTHENTICATION_BACKENDS:append": [
        "axes.backends.AxesStandaloneBackend",
        "angee.iam.auth.ModelBackend",
    ],
    # IAM calls REBAC actions directly and deliberately omits Django's optional
    # has_perm/codename bridge; its absence is not a configuration defect here.
    "SILENCED_SYSTEM_CHECKS:append": ["rebac.W001"],
}
"""Django settings contributed when IAM is installed."""


def settings(namespace: Mapping[str, Any]) -> dict[str, Any]:
    """Isolate local projects' cookies; browsers share localhost cookies across ports.

    Production and explicit cookie names retain their host-defined behavior.
    The project path is stable across process reloads and source workspaces.
    """

    result = {"REBAC_DEPTH_LIMIT": int(namespace.get(
        "REBAC_DEPTH_LIMIT", 8 if namespace.get("DATABASES", {}).get("default", {}).get("ENGINE")
        == "django.db.backends.sqlite3" else 16,
    ))}
    if not namespace.get("DEBUG") or not namespace.get("BASE_DIR"):
        return result
    suffix = hashlib.blake2s(str(namespace["BASE_DIR"]).encode(), digest_size=6).hexdigest()
    return result | {
        name: f"angee_{suffix}_{cookie}"
        for name, cookie in (("SESSION_COOKIE_NAME", "session"), ("CSRF_COOKIE_NAME", "csrf"))
        if name not in namespace
    }


def bootstrap_admin_settings() -> dict[str, str | None]:
    """Read explicit bootstrap inputs, preferring Django settings over the environment.

    Empty values fall through to the Angee environment name, then Django's
    createsuperuser name. Defaults belong to the command so provision can tell
    whether a deployment explicitly requested bootstrapping.
    """

    values: dict[str, str | None] = {}
    for field in ("username", "email", "password"):
        setting_name = f"ANGEE_BOOTSTRAP_ADMIN_{field.upper()}"
        value = getattr(django_settings, setting_name, None)
        if value in (None, ""):
            value = os.environ.get(setting_name) or os.environ.get(f"DJANGO_SUPERUSER_{field.upper()}")
        values[field] = str(value) if value not in (None, "") else None
    return values
