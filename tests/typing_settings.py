"""Isolated framework typing through native Django and composition settings.

Projects select their own authored isolated settings module through django-stubs.
This fixture always composes framework roots into its disposable mypy cache.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django_compose.addons import installed_addons

from angee.base.conf import configure

if os.environ.get("ANGEE_PROJECT_DIR"):
    raise ImproperlyConfigured(
        "tests.typing_settings is the isolated framework fixture; unset ANGEE_PROJECT_DIR. "
        "Projects select an authored isolated Django settings module through django-stubs."
    )

ASGI_APPLICATION: str
ANGEE_WEB_STATEMENT_TIMEOUT: int
CELERY_TASK_SOFT_TIME_LIMIT: int
CELERY_TASK_TIME_LIMIT: int

BASE_DIR = Path(__file__).resolve().parents[1]
SECRET_KEY = "framework-type-checking"
COMPOSE_OUTPUT_DIR = BASE_DIR / ".mypy_cache" / "typing_runtime"
COMPOSE_OUTPUT_MODULE = "typing_runtime"
INSTALLED_APPS = sorted(name for name in installed_addons("angee.addons").providers if name.startswith("angee."))
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ANGEE_MONEY_REFERENCE_CURRENCY = "USD"
configure(globals())
