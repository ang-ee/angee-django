"""Angee fixture metadata feeds native Django app identity and activation."""

from __future__ import annotations

from pathlib import Path

from django.apps import AppConfig
from django_compose.addons import enabled_apps

from angee.base.addons import is_angee_addon
from tests.conftest import make_addon


def test_fixture_foreign_dependencies_survive_native_config_rediscovery(tmp_path: Path) -> None:
    """A fixture AppConfig owns foreign edges before package activation."""

    declared = make_addon(
        name="example.forward", path=tmp_path / "forward", depends_on=("django.contrib.contenttypes",),
    )
    rediscovered = AppConfig.create(declared.name)

    assert type(rediscovered) is type(declared)
    assert rediscovered.requires_apps == ("django.contrib.contenttypes",)
    closure = enabled_apps((declared.name,), group="angee.addons")
    assert tuple(config.name for config in closure.app_configs) == ("django.contrib.contenttypes", declared.name)
    assert closure.addons == {declared.name}


def test_native_package_dependencies_activate_angee_fixture_configs(tmp_path: Path) -> None:
    """The fixture writer's Requires-Dist activates its actual Angee provider."""

    dependency = make_addon(name="example.required", path=tmp_path / "required")
    consumer = make_addon(name="example.consumer", path=tmp_path / "consumer", depends_on=(dependency.name,))
    closure = enabled_apps((consumer.name,), group="angee.addons")

    assert tuple(config.name for config in closure.app_configs) == (dependency.name, consumer.name)
    assert closure.required == {dependency.name}
    assert all(is_angee_addon(config) for config in closure.app_configs)
