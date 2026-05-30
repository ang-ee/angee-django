"""Tests for addon-owned resource loading."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, cast

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command, get_commands
from django.db import connection, models
from rebac import system_context

from angee.base.apps import BaseAddonConfig
from angee.base.models import AngeeModel
from angee.base.resources.exceptions import ResourceLoadError
from angee.base.resources.models import Resource


class ResourceConfig(BaseAddonConfig):
    """Tiny addon config used to exercise resource manifests."""

    name = "tests.resource_addon"
    label = "resource_addon"
    resources: ClassVar[dict[object, object]] = {
        Resource.Tier.INSTALL: ("resources/install.yaml",),
        "demo": "resources/demo.yaml",
    }


def config_for(tmp_path: Path) -> ResourceConfig:
    """Return a resource config with a concrete app root."""

    module = ModuleType(ResourceConfig.name)
    module.__file__ = str(tmp_path / "__init__.py")
    return ResourceConfig(ResourceConfig.name, module)


def resource_manager() -> Any:
    """Return the manager Django registers on the abstract source model."""

    return cast(Any, Resource._default_manager)


def test_base_addon_owns_resource_source_model() -> None:
    """Resource is an addon model owned by the base addon."""

    assert Resource.__module__ == "angee.base.resources.models"
    assert Resource in apps.get_app_config("base").model_classes


def test_resource_manifest_accepts_enum_keys_and_string_shorthand(
    tmp_path: Path,
) -> None:
    """Resource tiers are enum-owned while AppConfigs stay easy to author."""

    config = config_for(tmp_path)
    manifest = config.resource_manifest

    # Output keys are the normalized tier values; each entry normalizes to a
    # dict, regardless of how the AppConfig authored it (enum or string key,
    # bare path or mapping).
    assert manifest["master"] == ()
    assert manifest["install"] == ({"path": "resources/install.yaml"},)
    assert manifest["demo"] == ({"path": "resources/demo.yaml"},)


def test_resource_manifest_rejects_unknown_tiers(tmp_path: Path) -> None:
    """Unknown resource tiers fail at the manifest owner."""

    class BrokenConfig(ResourceConfig):
        resources: ClassVar[dict[object, object]] = {
            "fixture": ("resources/fixture.yaml",)
        }

    module = ModuleType(BrokenConfig.name)
    module.__file__ = str(tmp_path / "__init__.py")
    config = BrokenConfig(BrokenConfig.name, module)

    with pytest.raises(ImproperlyConfigured, match="Unknown resource tier"):
        config.resource_manifest


@pytest.mark.django_db(transaction=True)
def test_resources_load_rows_through_model_resources(
    tmp_path: Path,
) -> None:
    """Rows load through import-export widgets, including relationships."""

    class ImportUser(AngeeModel):
        username = models.CharField(max_length=40, unique=True)
        first_name = models.CharField(max_length=40, blank=True)

        class Meta:
            app_label = "base"

    class ImportNote(AngeeModel):
        title = models.CharField(max_length=80)
        tags = models.JSONField(default=list, blank=True)
        created_by = models.ForeignKey(ImportUser, on_delete=models.CASCADE)

        class Meta:
            app_label = "base"

    class ResourceLedger(Resource):
        class Meta(Resource.Meta):
            app_label = "base"
            abstract = False

    config = _resource_file_config(tmp_path)
    models_to_create = (ImportUser, ImportNote, ResourceLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        result = ResourceLedger.objects.load_addons(
            (config,),
            tiers=[Resource.Tier.MASTER],
        )

        with system_context(reason="resources-test"):
            alice = ImportUser.objects.get(username="alice")
            note = ImportNote.objects.get(title="Framework map")
            assert alice.first_name == "Alice"
            assert note.created_by == alice
            assert note.tags == ["composition", "resources"]
        ledger_xrefs = set(
            ResourceLedger.objects.values_list("xref", flat=True)
        )

        assert result.created == 2
        assert result.updated == 0
        assert result.skipped == 0
        assert result.loaded == 2
        assert ledger_xrefs == {"user_alice", "note_framework_map"}

        reload = ResourceLedger.objects.load_addons(
            (config,),
            tiers=[Resource.Tier.MASTER],
        )
        assert reload.created == 0
        assert reload.updated == 0
        assert reload.skipped == 2
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resources_dry_run_does_not_persist(tmp_path: Path) -> None:
    """Dry-run loads rows then rolls back the transaction."""

    class DryUser(AngeeModel):
        username = models.CharField(max_length=40, unique=True)

        class Meta:
            app_label = "base"

    class DryLedger(Resource):
        class Meta(Resource.Meta):
            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources" / "master"
    resource_dir.mkdir(parents=True)
    (resource_dir / "010_base.dryuser.csv").write_text(
        "_xref,username\ndry_alice,alice\n",
        encoding="utf-8",
    )

    class DryConfig(BaseAddonConfig):
        name = "tests.dry_addon"
        label = "dry_addon"
        resources: ClassVar[dict[object, object]] = {
            Resource.Tier.MASTER: ("resources/master/010_base.dryuser.csv",),
        }

    module = ModuleType(DryConfig.name)
    module.__file__ = str(tmp_path / "__init__.py")
    config = DryConfig(DryConfig.name, module)

    to_create = (DryUser, DryLedger)
    with connection.schema_editor() as schema_editor:
        for model in to_create:
            schema_editor.create_model(model)
    try:
        result = DryLedger.objects.load_addons(
            (config,),
            tiers=[Resource.Tier.MASTER],
            dry_run=True,
        )
        assert result.created == 1
        with system_context(reason="resources-test"):
            assert DryUser.objects.count() == 0
        assert DryLedger.objects.count() == 0
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_xref_collision_raises(tmp_path: Path) -> None:
    """Duplicate xrefs within the same addon are rejected."""

    class ColUser(AngeeModel):
        username = models.CharField(max_length=40, unique=True)

        class Meta:
            app_label = "base"

    class ColLedger(Resource):
        class Meta(Resource.Meta):
            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources" / "master"
    resource_dir.mkdir(parents=True)
    (resource_dir / "010_base.coluser.csv").write_text(
        "_xref,username\ndup_xref,alice\n",
        encoding="utf-8",
    )
    (resource_dir / "020_base.coluser.csv").write_text(
        "_xref,username\ndup_xref,bob\n",
        encoding="utf-8",
    )

    class CollisionConfig(BaseAddonConfig):
        name = "tests.collision_addon"
        label = "collision_addon"
        resources: ClassVar[dict[object, object]] = {
            Resource.Tier.MASTER: (
                "resources/master/010_base.coluser.csv",
                "resources/master/020_base.coluser.csv",
            ),
        }

    module = ModuleType(CollisionConfig.name)
    module.__file__ = str(tmp_path / "__init__.py")
    config = CollisionConfig(CollisionConfig.name, module)

    to_create = (ColUser, ColLedger)
    with connection.schema_editor() as schema_editor:
        for model in to_create:
            schema_editor.create_model(model)
    try:
        with pytest.raises(ResourceLoadError, match="xref collision"):
            ColLedger.objects.validate_addons(
                (config,), tiers=[Resource.Tier.MASTER]
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(to_create):
                schema_editor.delete_model(model)


def test_management_command_registered() -> None:
    """The angee_resources command is provided by the base addon."""

    assert get_commands()["angee_resources"] == "angee.base"

    output = StringIO()
    with pytest.raises(LookupError):
        call_command(
            "angee_resources", "diff", Resource.Tier.MASTER, stdout=output
        )


def _resource_file_config(tmp_path: Path) -> BaseAddonConfig:
    """Write a tiny resource manifest and return its addon config."""

    resource_root = tmp_path / "resources" / "master"
    resource_root.mkdir(parents=True)
    (resource_root / "010_base.importuser.csv").write_text(
        "_xref,username,first_name\nuser_alice,alice,Alice\n",
        encoding="utf-8",
    )
    (resource_root / "020_base.importnote.yaml").write_text(
        "- _xref: note_framework_map\n"
        "  title: Framework map\n"
        "  created_by: resource_addon.user_alice\n"
        "  tags:\n"
        "    - composition\n"
        "    - resources\n",
        encoding="utf-8",
    )

    class LoaderConfig(BaseAddonConfig):
        name = "tests.resource_addon"
        label = "resource_addon"
        resources: ClassVar[dict[object, object]] = {
            Resource.Tier.MASTER: (
                "resources/master/010_base.importuser.csv",
                "resources/master/020_base.importnote.yaml",
            ),
        }

    module = ModuleType(LoaderConfig.name)
    module.__file__ = str(tmp_path / "__init__.py")
    return LoaderConfig(LoaderConfig.name, module)
