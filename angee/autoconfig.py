"""Host bootstrap and request defaults for composed Angee applications."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "COMPOSE_APP_DECLARATIONS": str,
        "COMPOSE_ENV_PREFIXES": tuple[str, ...],
        "ROOT_URLCONF": str,
        "ASGI_APPLICATION": str,
        "MIDDLEWARE:prepend": list[str],
        "MIDDLEWARE:append": list[str],
        "ANGEE_WEB_STATEMENT_TIMEOUT": int,
    },
)

SETTINGS: _Settings = {
    "COMPOSE_ENV_PREFIXES": ("ANGEE_",),
    "COMPOSE_APP_DECLARATIONS": "angee.conf.ManifestDeclarations",
    "ROOT_URLCONF": "angee.urls",
    "ASGI_APPLICATION": "angee.asgi.application",
    "MIDDLEWARE:prepend": ["django.middleware.security.SecurityMiddleware"],
    "MIDDLEWARE:append": [
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csp.ContentSecurityPolicyMiddleware",
    ],
    "ANGEE_WEB_STATEMENT_TIMEOUT": 60,
}
