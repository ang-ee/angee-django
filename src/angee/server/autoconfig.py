"""HTTP defaults activate only through an enabled serving capability."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ROOT_URLCONF": str,
        "ASGI_APPLICATION": str,
        "MIDDLEWARE:prepend": list[str],
        "MIDDLEWARE:append": list[str],
        "ANGEE_WEB_STATEMENT_TIMEOUT": int,
        "ANGEE_SERVER_COMMAND_CONTEXT": list[str],
    },
)

SETTINGS: _Settings = {
    "ROOT_URLCONF": "angee.server.urls",
    "ASGI_APPLICATION": "angee.server.application",
    "MIDDLEWARE:prepend": ["django.middleware.security.SecurityMiddleware"],
    "MIDDLEWARE:append": [
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csp.ContentSecurityPolicyMiddleware",
    ],
    "ANGEE_WEB_STATEMENT_TIMEOUT": 60,
    # Enabled addons append dotted command-context factories; see ``angee.server.management``.
    "ANGEE_SERVER_COMMAND_CONTEXT": [],
}
