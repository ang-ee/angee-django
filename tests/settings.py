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
    "angee.workflows_integrate",
    "angee.knowledge",
    "angee.mcp",
    "angee.storage",
    "angee.storage_integrate",
    "angee.parties",
    "angee.money",
    "angee.scheduling",
    "angee.sequence",
    "angee.tags",
    "angee.uom",
    "angee.messaging",
    "angee.messaging_integrate_whatsapp",
    "angee.messaging_integrate_telegram",
    "angee.spaces",
    "angee.nexus",
    "angee.posts",
    "angee.platform",
    "angee.platform_integrate_vcs",
    # Every remaining source addon that declares models. Django resolves an
    # abstract model's app_label from the registry when the class is created, so
    # an addon whose models a test imports while its app is absent gets
    # app_label=None — permanently, for the process, whichever test imported it
    # first. `test_resource_fixtures` composes every addon's sources and needs
    # them all labelled; installing them here is what makes that independent of
    # test order. They contribute abstract sources only (bare test settings run
    # no composer), which is why this is a registry fact, not a model change.
    "angee.integrate_github",
    "angee.messaging_integrate_imap",
    "angee.operator",
    "angee.parties_integrate_carddav",
    "angee.platform_integrate_operator",
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
ANGEE_STORAGE_BACKEND_CLASSES = {
    "local": "angee.storage.backends.LocalBackend",
    "local_folder": "angee.storage_integrate.backends.LocalFolderBackend",
}
ANGEE_STORAGE_MOUNT_BACKEND_CLASSES = {
    "local_folder": "angee.storage_integrate.mounts.LocalFolderMountBackend",
}
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
    "ollama": "angee.agents_integrate_ollama.backend.OllamaInferenceBackend",
    "openai": "angee.agents_integrate_openai.backend.OpenAIInferenceBackend",
    "stub_inference": "tests.conftest.StubInferenceBackend",
}
ANGEE_AGENT_RUNTIME_CLASSES = {
    "none": "angee.agents.runtimes.NoRuntime",
    "claude_code": "angee.agents.runtimes.ClaudeCodeRuntime",
    "opencode": "angee.agents.runtimes.OpenCodeRuntime",
    "pydantic": "angee.agents_runtime_pydantic.runtime.PydanticAIRuntime",
}
ANGEE_WORKFLOW_STEP_CLASSES = {
    "handler": "angee.workflows.steps.HandlerStep",
    "wait": "angee.workflows.steps.WaitStep",
    "gate": "angee.workflows.steps.GateStep",
    "map": "angee.workflows.steps.MapStep",
    "agent": "angee.workflows_agents.steps.AgentStepImpl",
    "agent_session": "angee.workflows_agents.steps.AgentSessionStepImpl",
    "archive_probe": "angee.workflows_integrate.steps.ArchiveProbeStepImpl",
    "archive_gate": "angee.workflows_integrate.steps.ArchiveGateStepImpl",
    "archive_execute": "angee.workflows_integrate.steps.ArchiveExecuteStepImpl",
}
ANGEE_AGENT_TEARDOWN_HOOKS = ("angee.workflows_agents.sessions.close_agent_sessions",)
ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES = {
    "fixture_archive": "tests.test_workflows_integrate.FixtureArchiveExtractor",
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
    "carddav": "angee.parties_integrate_carddav.backend.CardDavDirectoryBackend",
}
ANGEE_CHANNEL_BACKEND_CLASSES = {
    "manual": "angee.messaging.backends.ManualChannelBackend",
    "imap": "angee.messaging_integrate_imap.backend.ImapChannelBackend",
    "telegram": "angee.messaging_integrate_telegram.backend.TelegramChannelBackend",
    "whatsapp": "angee.messaging_integrate_whatsapp.backend.WhatsAppChannelBackend",
    "fake_live": "tests.pairing_backend.FakePairingBackend",
}
# Feed backends a ``posts.Feed`` may select (posts' autoconfig normally
# contributes these). ``stub`` returns canned posts queued by the posts tests.
ANGEE_POSTS_FEED_BACKEND_CLASSES = {
    "manual": "angee.posts.backends.ManualFeedBackend",
    "stub": "tests.conftest.StubFeedBackend",
}
# OAuth provider types (normally each addon's autoconfig contributes these); the
# ImplClassField enum requires a non-empty registry at model-import time.
ANGEE_OAUTH_PROVIDER_TYPES = {
    "generic_oauth2": "angee.integrate.oauth.providers.GenericOAuth2",
    "generic_oidc": "angee.iam_integrate_oidc.providers.GenericOidc",
    "google": "angee.iam_integrate_oidc.providers.GoogleType",
}
ANGEE_CREDENTIAL_DISCONNECT_GUARDS = ("angee.iam_integrate_oidc.identity.guard_last_sign_in_disconnect",)
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
