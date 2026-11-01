"""Minimal IAM source registry; native composed-host checks follow integration."""

from tests.permissionless_settings import DATABASES as DATABASES
from tests.permissionless_settings import DEFAULT_AUTO_FIELD as DEFAULT_AUTO_FIELD
from tests.permissionless_settings import SECRET_KEY as SECRET_KEY
from tests.permissionless_settings import USE_TZ as USE_TZ

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "axes",
    "rebac",
    "angee.base",
    "tests.iam_only_app.IAMOnlyConfig",
    "tests.permissionless_app.apps.PermissionlessConfig",
    "tests.permissioned_app",
]
AUTH_USER_MODEL = "iam.User"
AUTHENTICATION_BACKENDS = ["angee.iam.auth.ModelBackend"]
REBAC_BACKEND = "local"
REBAC_LOCAL_BACKEND_STORAGE = "registry"
REBAC_STRICT_MODE = True
REBAC_ALLOW_SUDO = True
REBAC_SUPERUSER_BYPASS = False
REBAC_FIELD_READ_MODE = "redact"
REBAC_UNIVERSAL_ADMIN_ROLE = "angee/role:admin"
