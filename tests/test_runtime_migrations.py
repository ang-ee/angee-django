"""Addon-owned migrations materialized into Django runtime graphs."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db.migrations.loader import MigrationLoader

from angee.addons import AddonContract, AddonMigration
from angee.compose.migrations import RuntimeMigrations


def _write_module(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def runtime_migration_probe(tmp_path, monkeypatch, settings):
    runtime_module = f"runtime_{tmp_path.name}"
    runtime_dir = tmp_path / runtime_module
    source_root = tmp_path / "example" / "demo"
    for package in (
        tmp_path / "example",
        source_root,
        source_root / "migrations",
        runtime_dir,
        runtime_dir / "resources",
        runtime_dir / "resources" / "migrations",
    ):
        _write_module(package / "__init__.py")

    _write_module(
        runtime_dir / "resources" / "migrations" / "0001_legacy.py",
        """\
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Legacy",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("old_name", models.CharField(max_length=100)),
            ],
        ),
    ]
""",
    )
    source_path = source_root / "migrations" / "rename_legacy.py"
    _write_module(
        source_path,
        """\
from django.db import migrations


def applies(project_state):
    model = project_state.models.get(("resources", "legacy"))
    return model is not None and "old_name" in model.fields


def forwards(apps, schema_editor):
    apps.get_model("resources", "Legacy")


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.RenameField(
            model_name="legacy",
            old_name="old_name",
            new_name="new_name",
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
""",
    )

    for module_name in tuple(sys.modules):
        if module_name == "example" or module_name.startswith("example."):
            monkeypatch.delitem(sys.modules, module_name)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(settings.MIGRATION_MODULES, "resources", f"{runtime_module}.resources.migrations")
    importlib.invalidate_caches()
    addon = SimpleNamespace(
        name="example.demo",
        _addon_contract=AddonContract(
            name="example.demo",
            migrations=(AddonMigration("rename_legacy", "resources", "migrations.rename_legacy"),),
        ),
    )
    materializer = RuntimeMigrations(
        (addon,),
        runtime_dir=runtime_dir,
        runtime_module=runtime_module,
        labels=("resources",),
    )
    return materializer, addon, source_path, runtime_dir, source_root


def test_materialize_copies_complete_source_and_attaches_current_leaf(runtime_migration_probe) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe

    written = materializer.materialize()

    output = runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py"
    assert written == (output,)
    text = output.read_text(encoding="utf-8")
    assert text.startswith(source_path.read_text(encoding="utf-8"))
    assert "def forwards(apps, schema_editor):" in text
    assert 'Migration.dependencies.append(("resources", "0001_legacy"))' in text
    assert 'Migration.angee_origin = "example.demo:rename_legacy"' in text

    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert loader.graph.leaf_nodes("resources") == [("resources", "0002_rename_legacy")]
    state = loader.project_state()
    assert "new_name" in state.models["resources", "legacy"].fields
    assert "old_name" not in state.models["resources", "legacy"].fields


def test_applies_false_writes_nothing(runtime_migration_probe) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            'return model is not None and "old_name" in model.fields', "return False"
        ),
        encoding="utf-8",
    )
    importlib.invalidate_caches()

    assert materializer.materialize() == ()
    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def test_applicable_declarations_are_planned_sequentially(runtime_migration_probe) -> None:
    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(
        source_root / "migrations" / "add_marker.py",
        """\
from django.db import migrations, models


def applies(project_state):
    model = project_state.models.get(("resources", "legacy"))
    return model is not None and "new_name" in model.fields and "marker" not in model.fields


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.AddField(
            model_name="legacy",
            name="marker",
            field=models.BooleanField(default=False),
        ),
    ]
""",
    )
    addon._addon_contract = AddonContract(
        name="example.demo",
        migrations=(
            AddonMigration("rename_legacy", "resources", "migrations.rename_legacy"),
            AddonMigration("add_marker", "resources", "migrations.add_marker"),
        ),
    )
    importlib.invalidate_caches()

    written = materializer.materialize()

    assert [path.name for path in written] == ["0002_rename_legacy.py", "0003_add_marker.py"]
    second = (runtime_dir / "resources" / "migrations" / "0003_add_marker.py").read_text(encoding="utf-8")
    assert 'Migration.dependencies.append(("resources", "0002_rename_legacy"))' in second
