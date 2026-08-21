"""Minimal Django settings for framework-core tests."""

from __future__ import annotations

from pathlib import Path

from django.apps import AppConfig


class BareComposeConfig(AppConfig):
    """Register the compose core addon without emitting a generated runtime."""

    name = "angee.compose"
    label = "compose"


class BareGraphQLConfig(AppConfig):
    """Register the GraphQL core addon without process-wide ready hooks."""

    name = "angee.graphql"
    label = "graphql"


SECRET_KEY = "angee-tests"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rebac",
    "reversion",
    "simple_history",
    "tests.settings.BareComposeConfig",
    "angee.base",
    "tests.settings.BareGraphQLConfig",
    "angee.jobs",
    "tests.mtidemo",
]

_TEST_DB_DIR = Path(__file__).resolve().parent.parent / ".test-db"
_TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
_TEST_DB_FILE = str(_TEST_DB_DIR / "angee_pytest_db.sqlite3")
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _TEST_DB_FILE,
        "OPTIONS": {"timeout": 30, "init_command": "PRAGMA journal_mode=WAL;"},
        "TEST": {"NAME": _TEST_DB_FILE},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
ANGEE_RUNTIME_MODULE = "tests.runtime"
ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER = True
STRAWBERRY_DJANGO = {
    "DEFAULT_PK_FIELD_NAME": "sqid",
    "MAP_AUTO_ID_AS_GLOBAL_ID": False,
}
