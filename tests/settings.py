"""Minimal Django settings for backend unit tests."""

from __future__ import annotations

SECRET_KEY = "angee-tests"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rebac",
    "reversion",
    "simple_history",
    "angee.base",
    "angee.resources",
    "angee.iam",
    "angee.integrate",
    "angee.iam_integrate_oidc",
    "angee.agents",
    "angee.knowledge",
    "angee.mcp",
    "angee.storage",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
ANGEE_RUNTIME_MODULE = "tests.runtime"
# Bare test settings do not run the composer, so the ImplClassField registries
# (normally supplied by each addon's autoconfig) are declared explicitly here;
# the enum field requires each to be non-empty at model-import time.
ANGEE_STORAGE_BACKEND_CLASSES = {"local": "angee.storage.backends.LocalBackend"}
ANGEE_INTEGRATION_IMPLS = {
    "none": "angee.integrate.impl.NullIntegrationImpl",
    "local": "angee.integrate.vcs.backend.LocalVCSBackend",
    "stub": "tests.conftest.StubVCSBackend",
    "manual": "angee.agents.backends.ManualInferenceBackend",
    "stub_inference": "tests.conftest.StubInferenceBackend",
}
# OAuth provider types (normally each addon's autoconfig contributes these); the
# ImplClassField enum requires a non-empty registry at model-import time.
ANGEE_OAUTH_PROVIDER_TYPES = {
    "generic_oauth2": "angee.integrate.oauth.providers.GenericOAuth2",
    "generic_oidc": "angee.iam_integrate_oidc.providers.GenericOidc",
    "google": "angee.iam_integrate_oidc.providers.GoogleType",
}
# The agents-supplied bearer→actor verifier is composer autoconfig (angee.agents); a
# bare test settings module that skips the composer declares it so the verifier is
# wired. The MCP actor is bracketed around each tool call by
# angee.mcp.middleware.ActorMiddleware and read via rebac's ambient current_actor
# (no REBAC_MCP_ACTOR_RESOLVER override needed).
ANGEE_MCP_ACTOR_VERIFIER = "angee.agents.mcp_verifier.resolve_actor"
