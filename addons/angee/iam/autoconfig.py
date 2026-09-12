"""Settings fragments required by Angee IAM."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

SETTINGS = {
    "AUTH_USER_MODEL": "iam.User",
    # IAM owns the platform-admin role used by its schema and bootstrap command.
    "REBAC_UNIVERSAL_ADMIN_ROLE": "angee/role:admin",
    "MIDDLEWARE:append": [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "rebac.middleware.ActorMiddleware",
        "angee.iam.middleware.BearerTokenCsrfExemptMiddleware",
        "simple_history.middleware.HistoryRequestMiddleware",
        "reversion.middleware.RevisionMiddleware",
        "axes.middleware.AxesMiddleware",
    ],
    "AUTHENTICATION_BACKENDS:append": [
        "axes.backends.AxesStandaloneBackend",
        "rebac.backends.auth.RebacBackend",
        "angee.iam.auth.ModelBackend",
    ],
}
"""Django settings contributed when IAM is installed."""


def settings(namespace: Mapping[str, Any]) -> dict[str, str]:
    """Isolate local projects' cookies; browsers share localhost cookies across ports.

    Production and explicit cookie names retain their host-defined behavior.
    The project path is stable across process reloads and source workspaces.
    """

    if not namespace.get("DEBUG") or not namespace.get("BASE_DIR"):
        return {}
    suffix = hashlib.blake2s(str(namespace["BASE_DIR"]).encode(), digest_size=6).hexdigest()
    return {
        name: f"angee_{suffix}_{cookie}"
        for name, cookie in (("SESSION_COOKIE_NAME", "session"), ("CSRF_COOKIE_NAME", "csrf"))
        if name not in namespace
    }
