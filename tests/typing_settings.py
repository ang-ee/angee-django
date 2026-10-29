"""Compose an isolated host for django-stubs' native Django registry.

Source addons declare abstract model contracts. Type checking needs their real
concrete compositions so Django can resolve relation labels and inherited fields.
The normal composer owns that output, isolated from the live stack and pytest.
An explicit ANGEE_PROJECT_DIR reuses a project's declared roots and addon paths;
without it, the host keeps the complete framework composition.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path
from typing import Any

from angee.addons import addon_entry_points
from angee.build import ProjectBuildContract
from angee.compose.composer import Composer
from angee.conf import prepend_import_paths
from angee.project import PROJECT_DIR_ENV

# Types of composer/autoconfig-owned settings published below. Their values
# remain owned by Composer and the installed addons' autoconfig modules.
ASGI_APPLICATION: str
ANGEE_WEB_STATEMENT_TIMEOUT: int
COMPOSE_RUNTIME_DIR: Path
CELERY_TASK_SOFT_TIME_LIMIT: int
CELERY_TASK_TIME_LIMIT: int

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
if os.environ.get(PROJECT_DIR_ENV):
    _project: dict[str, Any] = {}
    ProjectBuildContract(_project).load()
    _ADDON_DIRS = _project["ANGEE_ADDON_DIRS"]
    _ROOT_APPS = _project["INSTALLED_APPS"]
    _CACHE_ROOT = Path(_project["BASE_DIR"]) / ".mypy_cache"
else:
    _ADDON_DIRS = (_SOURCE_ROOT / "addons",)
    _ROOT_APPS = sorted(name for name in addon_entry_points() if name.startswith("angee."))
    _CACHE_ROOT = _SOURCE_ROOT / ".mypy_cache"
_NAMESPACE = runpy.run_module(
    "angee.defaults",
    init_globals={
        "BASE_DIR": _CACHE_ROOT,
        "SECRET_KEY": "framework-type-checking",
        "COMPOSE_RUNTIME_DIR": _CACHE_ROOT / "typing_runtime",
        "COMPOSE_RUNTIME_MODULE": "typing_runtime",
        "ANGEE_ADDON_DIRS": _ADDON_DIRS,
        "INSTALLED_APPS": _ROOT_APPS,
        "DATABASES": {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        "ANGEE_MONEY_REFERENCE_CURRENCY": "USD",
    },
)
_NAMESPACE = {name: value for name, value in _NAMESPACE.items() if name.isupper() and not name.startswith("_")}
prepend_import_paths(_ADDON_DIRS)
_NAMESPACE["INSTALLED_APPS"].append("tests.typing_host.TypingHostConfig")
Composer(_NAMESPACE).compose_settings(compose_config="angee.build.ProjectBuildConfig")
globals().update(_NAMESPACE)
