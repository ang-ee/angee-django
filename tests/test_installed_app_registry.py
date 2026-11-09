"""The suite's registry guard gives every test the installed app registry it started with."""

import sys
from types import ModuleType

import pytest
from django.apps import apps
from django.db import models

from angee.base.testing.fixtures import installed_app_registry_restored


def test_models_a_test_creates_are_unregistered_with_their_through_models() -> None:
    with installed_app_registry_restored():

        class RegistryProbe(models.Model):
            groups = models.ManyToManyField("auth.Group", related_name="+")

            class Meta:
                app_label = "auth"

        through = RegistryProbe.groups.through
        assert apps.get_app_config("auth").models["registryprobe"] is RegistryProbe
        assert {RegistryProbe, through} <= set(apps.get_models(include_auto_created=True))

    assert "registryprobe" not in apps.get_app_config("auth").models
    assert not {RegistryProbe, through} & set(apps.get_models(include_auto_created=True))


def test_a_model_module_first_imported_in_the_block_keeps_its_models() -> None:
    module = ModuleType("tests.registry_probe_models")
    with pytest.MonkeyPatch.context() as patch:
        with installed_app_registry_restored():
            patch.setitem(sys.modules, module.__name__, module)
            imported = type(
                "RegistryImported",
                (models.Model,),
                {"__module__": module.__name__, "Meta": type("Meta", (), {"app_label": "auth"})},
            )

        assert apps.get_app_config("auth").models["registryimported"] is imported


def test_an_installed_app_config_a_test_removes_is_put_back() -> None:
    sessions = apps.get_app_config("sessions")
    with installed_app_registry_restored():
        del apps.app_configs["sessions"]

    assert apps.get_app_config("sessions") is sessions


def test_a_temporary_module_removed_before_restoration_does_not_keep_its_models() -> None:
    """The native monkeypatch lifetime ends inside the registry guard's lifetime."""

    with installed_app_registry_restored():
        with pytest.MonkeyPatch.context() as patch:
            module = ModuleType("tests.temporary_registry_models")
            patch.setitem(sys.modules, module.__name__, module)
            type(
                "TemporaryRegistryModel", (models.Model,),
                {"__module__": module.__name__, "Meta": type("Meta", (), {"app_label": "auth"})},
            )
    assert "temporaryregistrymodel" not in apps.get_app_config("auth").models
