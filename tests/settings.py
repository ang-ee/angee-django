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
    "angee.tasks",
    "angee.resources",
    "tests.iam_app.TestIAMConfig",
    "angee.integrate",
    "angee.iam_integrate_oidc",
    "angee.agents",
    "angee.workflows",
    "angee.workflows_agents",
    "angee.knowledge",
    "angee.mcp",
    "angee.storage",
    "angee.parties",
    "angee.money",
    "angee.scheduling",
    "angee.sequence",
    "angee.tags",
    "angee.uom",
    "angee.messaging",
    "angee.social",
    "angee.platform",
    "angee.platform_integrate_vcs",
    "tests.linesdemo",
    "tests.chatterdemo",
    "tests.scopedemo",
    "tests.extcontrib.apps.ExtContribConfig",
    "tests.mtidemo",
    "tests.hierdemo",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "iam.User"
USE_TZ = True
ANGEE_RUNTIME_MODULE = "tests.runtime"
ANGEE_STORAGE_DEFAULT_DRIVE = "assets"
ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES = 64 * 1024 * 1024
ANGEE_STORAGE_DRAFT_TTL_HOURS = 24
ANGEE_STORAGE_TRASH_TTL_DAYS = 30
# Bare test settings do not run the composer, so the ImplClassField registries
# (normally supplied by each addon's autoconfig) are declared explicitly here;
# the enum field requires each to be non-empty at model-import time.
ANGEE_STORAGE_BACKEND_CLASSES = {"local": "angee.storage.backends.LocalBackend"}
ANGEE_INTEGRATION_IMPLS = {
    "none": "angee.integrate.impl.NullIntegrationImpl",
}
ANGEE_RESOURCE_SOURCE_CLASSES = {
    "path": "angee.resources.sources.path_source",
    "url": "angee.integrate.resource_source.url_source",
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
ANGEE_WORKFLOW_STEP_CLASSES = {
    "handler": "angee.workflows.steps.HandlerStep",
    "wait": "angee.workflows.steps.WaitStep",
    "gate": "angee.workflows.steps.GateStep",
    "map": "angee.workflows.steps.MapStep",
    "agent": "angee.workflows_agents.steps.AgentStepImpl",
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
    "imap": "angee.messaging_integrate_imap.backend.ImapChannelBackend",
}
# Feed backends a ``social.Feed`` may select (social's autoconfig normally
# contributes these). ``stub`` returns canned posts queued by the social tests.
ANGEE_SOCIAL_FEED_BACKEND_CLASSES = {
    "manual": "angee.social.backends.ManualFeedBackend",
    "stub": "tests.conftest.StubFeedBackend",
}
# OAuth provider types (normally each addon's autoconfig contributes these); the
# ImplClassField enum requires a non-empty registry at model-import time.
ANGEE_OAUTH_PROVIDER_TYPES = {
    "generic_oauth2": "angee.integrate.oauth.providers.GenericOAuth2",
    "generic_oidc": "angee.iam_integrate_oidc.providers.GenericOidc",
    "google": "angee.iam_integrate_oidc.providers.GoogleType",
}
ANGEE_CREDENTIAL_DISCONNECT_GUARDS = (
    "angee.iam_integrate_oidc.identity.guard_last_sign_in_disconnect",
)
# Bare tests run Django's per-process LocMem cache. Production OAuth redirects
# must use a shared cache; tests opt in explicitly so the state guard remains loud.
ANGEE_INTEGRATE_ALLOW_LOCAL_OAUTH_STATE_CACHE = True
ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER = True
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
