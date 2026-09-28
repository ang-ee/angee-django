"""Minimal Django settings for framework-core and source-addon tests."""

from __future__ import annotations

import os
from pathlib import Path

import environ
from django.apps import AppConfig

from angee.compose.autoconfig import AutoConfig


class BareComposeConfig(AppConfig):
    """Register the core composer without emitting a generated runtime."""

    name = "angee.compose"
    label = "compose"


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
    "reversion",
    "simple_history",
    "tests.settings.BareComposeConfig",
    "angee.base",
    "tests.settings.BareGraphQLConfig",
    "django_celery_beat",
    "angee.jobs",
    "angee.resources",
    "tests.iam_app.TestIAMConfig",
    "angee.integrate",
    "angee.integrate_vcs",
    "angee.integrate_iphone",
    "angee.iam_integrate_oidc",
    "angee.agents",
    "angee.agents_integrate_anthropic",
    "angee.agents_integrate_openai",
    "angee.agents_integrate_ollama",
    "angee.agents_runtime_pydantic",
    "angee.workflows",
    "angee.decisions",
    "angee.knowledge",
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
    "angee.integrate.testing",
    "angee.workflows.testing",
    "angee.decisions.testing",
    "tests.linesdemo",
    "tests.chatterdemo",
    "tests.scopedemo",
    "tests.extcontrib.apps.ExtContribConfig",
    "tests.mtidemo",
    "tests.hierdemo",
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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
ANGEE_RUNTIME_MODULE = "tests.runtime"
ANGEE_ADDON_DIRS = (Path(__file__).resolve().parent.parent / "addons",)
# Bare tests run Django's per-process LocMem cache. Production OAuth redirects
# must use a shared cache; tests opt in explicitly so the state guard remains loud.
ANGEE_INTEGRATE_ALLOW_LOCAL_OAUTH_STATE_CACHE = True
ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER = True

AutoConfig.apply_installed(globals(), environment=False)
