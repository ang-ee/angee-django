"""Plain Django host for the relocated provenance engine's original tests."""

import os

SECRET_KEY = "provenance-engine-tests"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "simple_history",
    "tests.provenance_engine.apps.ProvenanceEngineTestConfig",
    "tests.provenance_engine.capture_app",
    "tests.provenance_engine.replay_app",
    "tests.provenance_engine.statements_app",
]
ANGEE_CHANGESET_MODEL = "provenance.Changeset"
MIGRATION_MODULES = {"provenance": "tests.provenance_engine.storage.migrations"}
PROVENANCE_STRICT = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
ROOT_URLCONF = "tests.provenance_engine.urls"
DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    "other": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}
if host := os.environ.get("PROVENANCE_TEST_DATABASE_HOST"):
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PROVENANCE_TEST_DATABASE_NAME", "provenance_engine"),
        "HOST": host,
        "PORT": os.environ.get("PROVENANCE_TEST_DATABASE_PORT", "5432"),
        "USER": os.environ.get("PROVENANCE_TEST_DATABASE_USER", "postgres"),
        "PASSWORD": os.environ.get("PROVENANCE_TEST_DATABASE_PASSWORD", ""),
    }
