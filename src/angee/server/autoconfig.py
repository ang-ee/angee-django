"""HTTP defaults activate only through an enabled serving capability."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypedDict

from angee.server import READINESS_ROUTE
from angee.server.profiling import profiling_permitted

_Settings = TypedDict(
    "_Settings",
    {
        "ROOT_URLCONF": str,
        "ASGI_APPLICATION": str,
        "MIDDLEWARE:prepend": list[str],
        "MIDDLEWARE:append": list[str],
        "ANGEE_WEB_STATEMENT_TIMEOUT": int,
        "ANGEE_SERVER_COMMAND_CONTEXT": list[str],
        "ANGEE_PROFILING_ALLOW_PRODUCTION": bool,
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
    # ``angee.server.E002`` refuses django-silk with DEBUG off unless this is the boolean True.
    "ANGEE_PROFILING_ALLOW_PRODUCTION": False,
}

_ProfilingSettings = TypedDict(
    "_ProfilingSettings",
    {
        "MIDDLEWARE:prepend": list[str],
        "TEMPLATES": list[dict[str, Any]],
        "SILKY_AUTHENTICATION": bool,
        "SILKY_AUTHORISATION": bool,
        "SILKY_PERMISSIONS": Callable[[Any], bool],
        "SILKY_IGNORE_PATHS": list[str],
        "SILKY_MAX_RECORDED_REQUESTS": int,
        "SILKY_META": bool,
    },
    total=False,
)


def settings(namespace: Mapping[str, Any]) -> _ProfilingSettings:
    """Profile live requests with django-silk when the project roots its ``silk`` app.

    Rooting ``silk`` in ``INSTALLED_APPS`` is the one opt-in: the compiler never lets an
    addon add a project root. Silk's middleware goes first, so no other middleware can
    answer a request it never sees and the queries of every inner middleware are recorded.
    Its own writes stay outside REBAC actor scope and provenance changesets. The template
    engine is Django's, with the request context processor silk's UI requires. Authored
    ``TEMPLATES`` and ``SILKY_*`` values win over these defaults.
    """

    if "silk" not in namespace["COMPOSE_CLOSURE"].dependencies:
        return {}
    return {
        "MIDDLEWARE:prepend": ["silk.middleware.SilkyMiddleware"],
        "TEMPLATES": [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
            }
        ],
        "SILKY_AUTHENTICATION": True,
        "SILKY_AUTHORISATION": True,
        "SILKY_PERMISSIONS": profiling_permitted,
        # The stack's readiness probe would otherwise fill the recorded requests.
        "SILKY_IGNORE_PATHS": [f"/{READINESS_ROUTE}"],
        # Each recorded request keeps its bodies and every statement's traceback.
        "SILKY_MAX_RECORDED_REQUESTS": 1_000,
        # Records silk's own overhead beside each request's timings.
        "SILKY_META": True,
    }
