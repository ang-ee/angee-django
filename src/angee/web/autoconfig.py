"""Build artifacts contributed by the optional web integration."""

from typing import Any, TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_UI": dict[str, Any] | None,
        "COMPOSE_INTEGRATIONS:append": list[str],
        "COMPOSE_FINGERPRINT_GLOBS:append": list[str],
        "COMPOSE_FINGERPRINT_SETTINGS:append": list[str],
        "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": list[str],
    },
)

SETTINGS: _Settings = {
    "ANGEE_UI": None,
    "COMPOSE_INTEGRATIONS:append": ["angee.web.build.WebIntegration"],
    "COMPOSE_FINGERPRINT_GLOBS:append": ["web/package.json"],
    # WebIntegration renders ANGEE_UI as the manifest's deployment layer, so the
    # snapshot holds it: `compose --if-stale` recomposes when it alone changes,
    # and, as a structural input, the environment cannot set it.
    "COMPOSE_FINGERPRINT_SETTINGS:append": ["ANGEE_UI"],
    "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": ["web"],
}
