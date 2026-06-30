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
    "angee.parties",
    "angee.messaging",
    "angee.platform",
    "angee.platform_integrate_vcs",
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
}
ANGEE_VCS_BACKEND_CLASSES = {
    "local": "angee.integrate.vcs.backend.LocalVCSBackend",
    "stub": "tests.conftest.StubVCSBackend",
}
ANGEE_INFERENCE_BACKEND_CLASSES = {
    "manual": "angee.agents.backends.ManualInferenceBackend",
    "anthropic": "angee.agents_integrate_anthropic.backend.AnthropicInferenceBackend",
    "openai": "angee.agents_integrate_openai.backend.OpenAIInferenceBackend",
    "stub_inference": "tests.conftest.StubInferenceBackend",
}
ANGEE_AGENT_RUNTIME_CLASSES = {
    "none": "angee.agents.runtimes.NoRuntime",
    "claude_code": "angee.agents.runtimes.ClaudeCodeRuntime",
    "opencode": "angee.agents.runtimes.OpenCodeRuntime",
}
ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES = {
    "lexical": "angee.knowledge.retrieval.LexicalRetrievalBackend",
}
# The AddonInstaller backend registry (normally platform's autoconfig contributes
# these). Bare test settings skip the composer, so the row-less ImplClassField-style
# registry is declared explicitly here; ``local`` is the dev/test default.
ANGEE_ADDON_INSTALLER_BACKEND = "local"
ANGEE_ADDON_INSTALLER_BACKEND_CLASSES = {
    "local": "angee.platform.installer.LocalInstallerBackend",
    "operator": "angee.platform.installer.OperatorInstallerBackend",
}
# Directory/channel backends each addon's autoconfig normally contributes; declared
# here so the ImplClassField registries are non-empty at model-import time.
ANGEE_DIRECTORY_BACKEND_CLASSES = {
    "manual": "angee.parties.backends.ManualDirectoryBackend",
}
ANGEE_CHANNEL_BACKEND_CLASSES = {
    "manual": "angee.messaging.backends.ManualChannelBackend",
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
STRAWBERRY_DJANGO = {
    # Mirror the composer-owned public ID contract for source-addon tests that
    # bypass compose settings.
    "DEFAULT_PK_FIELD_NAME": "sqid",
    "MAP_AUTO_ID_AS_GLOBAL_ID": False,
}
