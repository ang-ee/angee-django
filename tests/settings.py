"""Minimal Django settings for framework-core tests."""

from __future__ import annotations

import os
from pathlib import Path

import environ
from django.apps import AppConfig


class BareComposeConfig(AppConfig):
    """Register the core composer without emitting a generated runtime."""

    name = "angee.compose"
    label = "compose"


SECRET_KEY = "angee-tests"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rebac",
    "reversion",
    "simple_history",
    "tests.settings.BareComposeConfig",
    "angee.base",
    "angee.jobs",
    "tests.mtidemo",
]

_TEST_DB_DIR = Path(__file__).resolve().parent.parent / ".test-db"
_TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
_TEST_DB_FILE = str(_TEST_DB_DIR / "angee_pytest_db.sqlite3")
if database_url := os.environ.get("DATABASE_URL"):
    DATABASES = {"default": environ.Env.db_url_config(database_url)}
else:
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
