"""Tests for the framework-owned Django settings defaults."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from angee.compose.autoconfig import AutoConfig
from angee.compose.composer import Composer

CORE_APP_NAMES = [
    "django_yamlconf",
    "angee.compose",
    "django.contrib.contenttypes",
    "rebac",
    "reversion",
    "simple_history",
    "angee.base",
    "django_celery_beat",
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


@pytest.mark.parametrize("composed", (False, True), ids=("bare", "composed"))
@pytest.mark.parametrize(
    ("engine", "depth", "environment_depth", "expected"),
    (
        (None, None, None, 8),
        ("sqlite3", None, None, 8),
        ("postgresql", None, None, 16),
        ("sqlite3", 4, None, 4),
        ("postgresql", 4, None, 4),
        ("sqlite3", None, "12", 12),
        ("postgresql", None, "12", 12),
        ("sqlite3", 4, "12", 12),
        ("postgresql", 4, "12", 12),
    ),
    ids=(
        "sqlite-fallback", "sqlite", "postgresql", "sqlite-explicit", "postgresql-explicit",
        "sqlite-environment", "postgresql-environment",
        "sqlite-project-and-environment", "postgresql-project-and-environment",
    ),
)
def test_rebac_depth_uses_explicit_settings_before_backend_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, composed: bool,
    engine: str | None, depth: int | None, environment_depth: str | None, expected: int,
) -> None:
    """Environment, then project depth, takes precedence over backend defaults."""

    monkeypatch.delenv("REBAC_DEPTH_LIMIT", raising=False)
    if environment_depth is not None:
        monkeypatch.setenv("REBAC_DEPTH_LIMIT", environment_depth)
    namespace = {"BASE_DIR": tmp_path, "ANGEE_RUNTIME_DIR": tmp_path / "runtime"}
    if engine is not None:
        namespace["DATABASES"] = {"default": {"ENGINE": f"django.db.backends.{engine}", "NAME": "test"}}
    if depth is not None:
        namespace["REBAC_DEPTH_LIMIT"] = depth
    defaults = runpy.run_module("angee.compose.defaults", init_globals=namespace, run_name="__test_rebac_defaults__")
    namespace = {name: value for name, value in defaults.items() if name.isupper() and not name.startswith("_")}
    if composed:
        Composer(namespace).compose_settings()
    else:
        AutoConfig.apply_installed(namespace)

    assert namespace["REBAC_DEPTH_LIMIT"] == expected
    assert isinstance(namespace["REBAC_DEPTH_LIMIT"], int)
