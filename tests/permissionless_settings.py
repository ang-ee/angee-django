"""Django source-fixture settings: contenttypes and base, with no auth or IAM."""

SECRET_KEY = "permissionless-tests"
INSTALLED_APPS = ["django.contrib.contenttypes", "angee.base", "tests.permissionless_app.apps.PermissionlessConfig"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
