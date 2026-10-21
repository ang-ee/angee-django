"""Compose the full framework host for django-stubs' native Django registry.

Source addons declare abstract model contracts. Type checking needs their real
concrete compositions so Django can resolve relation labels and inherited fields.
The normal composer owns that output, isolated from the live stack and pytest.
"""

from __future__ import annotations

import runpy
from pathlib import Path

from hatch_angee import discover

from angee.compose.composer import Composer

# Types of composer/autoconfig-owned settings published below. Their values
# remain owned by Composer and the installed addons' autoconfig modules.
ASGI_APPLICATION: str
ANGEE_WEB_STATEMENT_TIMEOUT: int
ANGEE_RUNTIME_DIR: Path
CELERY_TASK_SOFT_TIME_LIMIT: int
CELERY_TASK_TIME_LIMIT: int

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_ADDON_DIRS = (_SOURCE_ROOT / "addons",)
_NAMESPACE = runpy.run_module(
    "angee.compose.defaults",
    init_globals={
        "BASE_DIR": _SOURCE_ROOT / ".mypy_cache",
        "SECRET_KEY": "framework-type-checking",
        "ANGEE_RUNTIME_DIR": _SOURCE_ROOT / ".mypy_cache" / "typing_runtime",
        "ANGEE_RUNTIME_MODULE": "typing_runtime",
        "ANGEE_ADDON_DIRS": _ADDON_DIRS,
        "INSTALLED_APPS": sorted(manifest.name for _, manifest in discover(_ADDON_DIRS)),
        "DATABASES": {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        "ANGEE_MONEY_REFERENCE_CURRENCY": "USD",
    },
)
_NAMESPACE = {name: value for name, value in _NAMESPACE.items() if name.isupper() and not name.startswith("_")}
Composer(_NAMESPACE).compose_settings()
_NAMESPACE["INSTALLED_APPS"].append("tests.typing_host.TypingHostConfig")
globals().update(_NAMESPACE)
