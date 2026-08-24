"""Tests for the framework-owned Django settings defaults."""

from __future__ import annotations

import runpy
from pathlib import Path

from angee.compose.composer import Composer

CORE_APP_NAMES = [
    "django_yamlconf",
    "angee.compose",
    "django.contrib.contenttypes",
    "rebac",
    "reversion",
    "simple_history",
    "angee.base",
    "angee.jobs",
]


def test_defaults_prefixes_the_ordered_core_app_set(tmp_path: Path) -> None:
    """Core and its Django app dependencies are always on before project roots."""

    defaults = runpy.run_module(
        "angee.compose.defaults",
        init_globals={"BASE_DIR": tmp_path, "INSTALLED_APPS": ("example.product",)},
        run_name="__test_core_defaults__",
    )

    assert defaults["INSTALLED_APPS"] == [*CORE_APP_NAMES, "example.product"]


def test_core_fixture_composes_to_the_historical_app_order(tmp_path: Path) -> None:
    """The former core-addon closure and the static prefix resolve identically."""

    settings = {
        "INSTALLED_APPS": (*CORE_APP_NAMES,),
        "ANGEE_RUNTIME_DIR": tmp_path / "runtime",
    }

    Composer(settings).compose_settings()

    assert [config.name for config in settings["INSTALLED_APPS"]] == CORE_APP_NAMES
