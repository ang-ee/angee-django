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
