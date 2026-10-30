"""Bare Django source-model host with native addon settings contributions."""

from __future__ import annotations

import os
from pathlib import Path

import environ
from django.apps import AppConfig
from django_compose.addons import enabled_apps
from django_compose.conf import apply_app_settings


class BareGraphQLConfig(AppConfig):
    """Register the GraphQL folder addon without process-wide ready hooks."""

    name = "angee.graphql"
    label = "graphql"


SECRET_KEY = "angee-tests"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "axes",
    "rebac",
    "simple_history",
    "angee.base",
    "angee.web",
    "angee.provenance",
    "tests.settings.BareGraphQLConfig",
    "angee.timeline",
    "django_celery_beat",
    "angee.jobs",
    "angee.resources",
    "tests.iam_app.TestIAMConfig",
    "angee.integrate",
    "angee.integrate_vcs",
    "angee.iam_integrate_oidc",
    "angee.agents",
    "angee.agents_integrate_anthropic",
    "angee.agents_integrate_openai",
    "angee.agents_integrate_ollama",
    "angee.agents_runtime_pydantic",
    "angee.workflows",
    "angee.decisions",
    "angee.extraction",
    "angee.workflows_extraction",
    "angee.workflows_integrate",
    "angee.knowledge",
    "angee.knowledge_integrate_notion",
    "angee.mcp",
    "angee.storage",
    "angee.storage_integrate",
    "angee.storage_integrate_iphone",
    "angee.parties",
    "angee.money",
    "angee.scheduling",
    "angee.sequence",
    "angee.tags",
    "angee.uom",
    "angee.messaging",
    "angee.projects",
    "angee.portfolio",
    "angee.proposals",
    "angee.messaging_integrate_slack",
    "angee.spaces",
    "angee.work",
    "angee.intake",
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
    "angee.resources.testing",
    "angee.provenance.testing",
    "angee.integrate.testing",
    "angee.workflows.testing",
    "tests.linesdemo",
    "tests.chatterdemo",
    "tests.scopedemo",
    "tests.extcontrib.apps.ExtContribConfig",
    "tests.mtidemo",
    "tests.hierdemo",
    "tests.recordrefdemo",
    "tests",
]
# Checkout- and process-local so concurrent pytest runs never share one SQLite
# file. Threads within a run still share its file-backed database. `.test-db/`
# is purpose-named and gitignored.
_TEST_DB_DIR = Path(__file__).resolve().parent.parent / ".test-db"
_TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
_TEST_DB_FILE = str(_TEST_DB_DIR / f"angee_pytest_{os.getpid()}.sqlite3")
if database_url := os.environ.get("DATABASE_URL"):
    DATABASES = {"default": environ.Env.db_url_config(database_url)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            # A file-backed test DB (not ":memory:") so each thread gets its own
            # connection. Threaded session tests (Matrix/Telegram/Signal live sessions)
            # write the bridge row from a worker thread while the test's operator thread
            # writes too; production is Postgres, where those serialize on a row lock. A
            # shared in-memory SQLite connection instead raises "database table is locked".
            # With a file DB + busy timeout each writer waits for the other, matching
            # production. WAL keeps concurrent reads non-blocking.
            "NAME": _TEST_DB_FILE,
            "OPTIONS": {"timeout": 30, "init_command": "PRAGMA journal_mode=WAL;"},
            "TEST": {"NAME": _TEST_DB_FILE},
        }
    }
# Bare tests run Django's per-process LocMem cache. Production OAuth redirects
# must use a shared cache; tests opt in explicitly so the state guard remains loud.
ANGEE_INTEGRATE_ALLOW_LOCAL_OAUTH_STATE_CACHE = True
ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER = True
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# These are ordinary Django source-fixture inputs. Composed-host tests exercise
# native emission and generated receipts in fresh interpreters.
BASE_DIR = Path(__file__).resolve().parent.parent
AUTH_USER_MODEL = "iam.User"
AUTHENTICATION_BACKENDS = ["angee.iam.auth.ModelBackend"]
ANGEE_CHANGESET_MODEL = "provenance.Changeset"
REBAC_BACKEND = "local"
REBAC_LOCAL_BACKEND_STORAGE = "registry"
REBAC_STRICT_MODE = True
REBAC_FIELD_READ_MODE = "redact"
REBAC_ALLOW_SUDO = True
REBAC_SUPERUSER_BYPASS = False
REBAC_UNIVERSAL_ADMIN_ROLE = "angee/role:admin"
REBAC_LINT_BARE_PREFETCH = False
CELERY_BROKER_URL = "memory://"

# Apply source app declarations without emitting models or replacing this registry.
apply_app_settings(globals(), enabled_apps(INSTALLED_APPS, group="angee.addons"))
