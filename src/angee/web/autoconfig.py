"""Build artifacts contributed by the optional web integration."""

SETTINGS = {
    "COMPOSE_INTEGRATIONS:append": ["angee.web.build.WebIntegration"],
    "COMPOSE_FINGERPRINT_GLOBS:append": ["web/package.json"],
    "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": ["web"],
}
