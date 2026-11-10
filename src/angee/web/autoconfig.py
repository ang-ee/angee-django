"""Build artifacts contributed by the optional web integration."""

from typing import Any, TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_UI": dict[str, Any] | None,
        "COMPOSE_INTEGRATIONS:append": list[str],
        "COMPOSE_FINGERPRINT_GLOBS:append": list[str],
        "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": list[str],
    },
)

SETTINGS: _Settings = {
    "ANGEE_UI": None,
    "COMPOSE_INTEGRATIONS:append": ["angee.web.build.WebIntegration"],
    "COMPOSE_FINGERPRINT_GLOBS:append": ["web/package.json"],
    "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": ["web"],
}
