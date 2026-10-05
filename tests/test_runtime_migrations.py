"""Addon-owned migrations materialized into Django runtime graphs."""

from __future__ import annotations

import hashlib
import importlib
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.apps import apps
from django.core.management.base import CommandError
from django.db import connection, connections, migrations, models
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.dummy.base import DatabaseWrapper as DummyDatabaseWrapper
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.questioner import MigrationQuestioner
from django.db.migrations.state import ModelState, ProjectState
from django.db.migrations.writer import MigrationWriter
from django.db.models.functions import Lower

from angee.base.fields import StateField
from angee.compose.migrations import DropGuardAutodetector, RuntimeMigrations
from angee.storage.models import Folder
from tests.conftest import make_addon, write_addon_manifest


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
        source_root / "runtime_migrations",
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
    source_path = source_root / "runtime_migrations" / "rename_legacy.py"
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
    addon = make_addon(
        name="example.demo",
        path=source_root,
        migrations=({"name": "rename_legacy", "app_label": "resources", "module": "runtime_migrations.rename_legacy"},),
    )
    materializer = RuntimeMigrations(
        (addon,),
        runtime_dir=runtime_dir,
        labels=("resources",),
    )
    return materializer, addon, source_path, runtime_dir, source_root


def test_materialize_copies_complete_source_and_attaches_current_leaf(runtime_migration_probe) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe

    written = materializer.materialize(apps=apps)

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


@pytest.mark.parametrize(("installed", "opt_in", "expected"), [
    (False, True, True), (False, False, False), (True, True, False), (True, False, True),
])
def test_uninstalled_cutover_requires_opt_in_and_never_enables_capabilities(
    runtime_migration_probe, settings, installed, opt_in, expected,
):
    materializer, addon, _, _, source_root = runtime_migration_probe
    write_addon_manifest(addon, migrations=({
        "name": "rename_legacy", "app_label": "resources", "module": "runtime_migrations.rename_legacy",
        "uninstalled_only": opt_in,
    },))
    (source_root / "autoconfig.py").write_text('raise AssertionError("uninstalled autoconfig loaded")\n')
    (source_root / "models.py").write_text('raise AssertionError("uninstalled models loaded")\n')
    settings.ANGEE_ADDON_DIRS = (source_root.parent.parent,)
    if not installed:
        materializer.addons = ()
    written = materializer.materialize(apps=None)
    assert bool(written) is expected
    assert not any(config.name == addon.name for config in apps.get_app_configs())


def test_applies_false_writes_nothing(runtime_migration_probe, caplog) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            'return model is not None and "old_name" in model.fields', "return False"
        ),
        encoding="utf-8",
    )
    importlib.invalidate_caches()

    current_apps = MigrationLoader(None, ignore_no_migrations=True).project_state().apps
    with caplog.at_level(logging.WARNING, logger="angee.compose.migrations"):
        assert materializer.materialize(apps=current_apps) == ()
    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()
    assert caplog.record_tuples == [
        (
            "angee.compose.migrations",
            logging.WARNING,
            "example.demo:rename_legacy (app label resources): runtime migration never became applicable; skipped",
        ),
    ]


@pytest.mark.parametrize("consumer_cutover", [False, True])
def test_adopted_table_drop_requires_consumer_cutover(
    runtime_migration_probe, monkeypatch, settings, caplog, consumer_cutover: bool
) -> None:
    """Adoption may stage, while physical-owner cleanup waits for consumer cutover."""

    _, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(runtime_dir / "integrate_vcs" / "__init__.py")
    _write_module(runtime_dir / "integrate_vcs" / "migrations" / "__init__.py")
    monkeypatch.setitem(settings.MIGRATION_MODULES, "integrate_vcs", f"{runtime_dir.name}.integrate_vcs.migrations")
    _write_module(
        runtime_dir / "resources" / "migrations" / "0002_consumer.py",
        """from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [("resources", "0001_legacy")]
    operations = [migrations.CreateModel(name="Consumer", fields=[
        ("id", models.AutoField(primary_key=True)),
        ("legacy", models.ForeignKey("resources.Legacy", on_delete=models.CASCADE)),
    ])]
""",
    )
    _write_module(
        source_root / "adopt_legacy.py",
        """from django.db import migrations, models
def applies(state):
    return ("integrate_vcs", "legacy") not in state.models
class Migration(migrations.Migration):
    dependencies = [("resources", "__latest__")]
    operations = [migrations.SeparateDatabaseAndState(state_operations=[
        migrations.CreateModel(name="Legacy", fields=[
            ("id", models.AutoField(primary_key=True)),
            ("old_name", models.CharField(max_length=100)),
        ], options={"db_table": "resources_legacy"}),
    ])]
""",
    )
    _write_module(
        source_root / "delete_legacy.py",
        """from django.db import migrations
def applies(state):
    return (
        ("resources", "legacy") in state.models
        and ("integrate_vcs", "legacy") in state.models
        and state.models["resources", "consumer"].fields["legacy"].remote_field.model.lower() == "integrate_vcs.legacy"
    )
class Migration(migrations.Migration):
    dependencies = [("integrate_vcs", "__latest__")]
    operations = [migrations.SeparateDatabaseAndState(state_operations=[migrations.DeleteModel(name="Legacy")])]
""",
    )
    declarations = [
        dict(name="delete_legacy", app_label="resources", module="delete_legacy"),
        dict(name="adopt_legacy", app_label="integrate_vcs", module="adopt_legacy"),
    ]
    if consumer_cutover:
        _write_module(
            source_root / "consumer_cutover.py",
            """from django.db import migrations, models
def applies(state):
    return (
        ("integrate_vcs", "legacy") in state.models
        and state.models["resources", "consumer"].fields["legacy"].remote_field.model.lower() == "resources.legacy"
    )
class Migration(migrations.Migration):
    dependencies = [("integrate_vcs", "__latest__")]
    operations = [migrations.SeparateDatabaseAndState(state_operations=[migrations.AlterField(
        model_name="consumer", name="legacy",
        field=models.ForeignKey("integrate_vcs.Legacy", on_delete=models.CASCADE),
    )])]
""",
        )
        declarations.append(dict(name="consumer_cutover", app_label="resources", module="consumer_cutover"))
    write_addon_manifest(addon, migrations=tuple(declarations))
    importlib.invalidate_caches()
    materializer = RuntimeMigrations((addon,), runtime_dir=runtime_dir, labels=("resources", "integrate_vcs"))

    current = MigrationLoader(None, ignore_no_migrations=True).project_state()
    adopted = current.models["resources", "legacy"].clone()
    adopted.app_label = "integrate_vcs"
    adopted.options["db_table"] = "resources_legacy"
    current.add_model(adopted)
    current.alter_field(
        "resources",
        "consumer",
        "legacy",
        models.ForeignKey("integrate_vcs.Legacy", on_delete=models.CASCADE),
        preserve_default=True,
    )
    current.remove_model("resources", "legacy")

    if consumer_cutover:
        written = materializer.materialize(apps=current.apps)
        assert [path.name for path in written] == [
            "0001_adopt_legacy.py",
            "0003_consumer_cutover.py",
            "0004_delete_legacy.py",
        ]
        assert materializer.materialize(apps=current.apps) == ()
        materializer.check()
        assert not caplog.records
    else:
        with pytest.raises(RuntimeError) as error:
            materializer.materialize(apps=current.apps)
        assert "resources.legacy: DeleteModel" in str(error.value)
        assert "table 'resources_legacy', still owned by integrate_vcs.legacy" in str(error.value)
        assert "Never-applicable runtime migration declarations: example.demo:delete_legacy" in str(error.value)
        assert "cutover migration" in str(error.value)
        assert (runtime_dir / "integrate_vcs" / "migrations" / "0001_adopt_legacy.py").exists()
        assert not (runtime_dir / "resources" / "migrations" / "0003_delete_legacy.py").exists()


def _donor_states() -> tuple[ProjectState, ProjectState, MigrationGraph]:
    """History with legacy donor columns, current models without them, and its graph."""

    def task(*donated: tuple[str, models.Field]) -> ModelState:
        fields = [("id", models.AutoField(primary_key=True)), ("title", models.CharField(max_length=20))]
        return ModelState("probe", "Task", [*fields, *donated])

    history, current = ProjectState(), ProjectState()
    for state in (history, current):
        state.add_model(ModelState("probe", "Target", [("id", models.AutoField(primary_key=True))]))
        state.add_model(ModelState("probe", "Link", [
            ("id", models.AutoField(primary_key=True)),
            ("task", models.ForeignKey("probe.Task", models.CASCADE, related_name="+")),
            ("target", models.ForeignKey("probe.Target", models.CASCADE, related_name="+")),
        ]))
    history.add_model(task(
        ("round", models.ForeignKey("probe.Target", models.SET_NULL, null=True, related_name="+")),
        ("kind", models.CharField(max_length=20, default="")),
        ("data", models.JSONField(default=dict)),
        ("version", models.PositiveIntegerField(default=0)),
        ("admins_only", models.BooleanField(default=False)),
        ("tags", models.ManyToManyField("probe.Target", related_name="+")),
        # Stored elsewhere or derived: neither refuses its removal.
        ("linked", models.ManyToManyField("probe.Target", through="probe.Link", related_name="+")),
        ("slug", models.GeneratedField(
            expression=Lower("title"), output_field=models.CharField(max_length=20), db_persist=True,
        )),
    ))
    current.add_model(task())
    graph = MigrationGraph()
    graph.add_node(("probe", "0001_initial"), migrations.Migration("0001_initial", "probe"))
    return history, current, graph


@pytest.mark.parametrize(("field", "value", "storage"), [
    (None, None, None),
    ("round", "target", "column probe_task.round_id"),
    ("kind", "kept", "column probe_task.kind"),
    ("data", {"kept": True}, "column probe_task.data"),
    ("version", 3, "column probe_task.version"),
    ("admins_only", True, "column probe_task.admins_only"),
    ("tags", "target", "table probe_task_tags"),
])
@pytest.mark.django_db
@pytest.mark.usefixtures("isolated_upgrade_database")
def test_autodetected_column_drop_requires_every_row_empty(field, value, storage) -> None:
    """NULL or the declared default is empty; any other stored value refuses the drop."""

    history, current, graph = _donor_states()
    target_model, task_model, link_model = (
        history.apps.get_model("probe", name) for name in ("Target", "Task", "Link")
    )
    with connection.schema_editor() as editor:
        for model in (target_model, task_model, link_model):
            editor.create_model(model)
    target = target_model._base_manager.create()
    task, _ = (task_model._base_manager.create(title=title) for title in ("first", "second"))
    link_model._base_manager.create(task=task, target=target)
    if field == "tags":
        task.tags.add(target)
    elif field is not None:
        task_model._base_manager.filter(pk=task.pk).update(**{field: target if value == "target" else value})

    guard = DropGuardAutodetector(history, current)
    if field is None:
        (migration,) = guard.changes(graph=graph)["probe"]
        assert sorted(operation.name for operation in migration.operations) == [
            "admins_only", "data", "kind", "linked", "round", "slug", "tags", "version",
        ]
        return
    with pytest.raises(CommandError) as refused:
        guard.changes(graph=graph)
    message = str(refused.value)
    assert message.startswith(
        f"unsafe autodetected column drop: probe.task.{field}: {storage} holds data in 1 row(s) on database 'default'. "
    )
    assert "[[migrations]]" in message


@pytest.mark.parametrize(("name", "wrapper"), [
    ("missing/db.sqlite3", DatabaseWrapper),
    ("", DummyDatabaseWrapper),
], ids=["unreachable", "unconfigured"])
@pytest.mark.django_db
def test_column_drop_is_refused_when_no_database_can_prove_it_empty(tmp_path, name, wrapper) -> None:
    """Changes without a drop need no database; an unprovable drop writes nothing."""

    history, current, graph = _donor_states()
    added = current.clone()
    added.add_field("probe", "task", "note", models.TextField(default=""), preserve_default=True)
    with _default_database(str(tmp_path / name) if name else "", wrapper):
        assert DropGuardAutodetector(current, added).changes(graph=graph)
        unprovable = r"probe\.task\.kind: cannot prove column probe_task\.kind empty on database 'default' \("
        with pytest.raises(CommandError, match=unprovable):
            DropGuardAutodetector(history, current).changes(graph=graph)


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.django_db
@pytest.mark.usefixtures("isolated_upgrade_database")
def test_declared_cutover_migration_owns_a_populated_column_drop(runtime_migration_probe, declared: bool) -> None:
    """A materialized addon cutover removes the field, so nothing is autodetected."""

    _, addon, _, runtime_dir, source_root = runtime_migration_probe
    declarations = []
    if declared:
        _write_module(
            source_root / "runtime_migrations" / "retire_old_name.py",
            """from django.db import migrations
def applies(project_state):
    model = project_state.models.get(("resources", "legacy"))
    return model is not None and "old_name" in model.fields
class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RemoveField(model_name="legacy", name="old_name")]
""",
        )
        declarations.append(
            dict(name="retire_old_name", app_label="resources", module="runtime_migrations.retire_old_name"),
        )
    write_addon_manifest(addon, migrations=declarations)
    importlib.invalidate_caches()
    history = MigrationLoader(None, ignore_no_migrations=True).project_state()
    legacy = history.apps.get_model("resources", "Legacy")
    with connection.schema_editor() as editor:
        editor.create_model(legacy)
    legacy._base_manager.create(old_name="kept")
    current = history.clone()
    current.remove_field("resources", "legacy", "old_name")

    materializer = RuntimeMigrations((addon,), runtime_dir=runtime_dir, labels=("resources",))
    written = materializer.materialize(apps=current.apps)
    loader = MigrationLoader(None, ignore_no_migrations=True)
    guard = DropGuardAutodetector(loader.project_state(), current)
    if declared:
        assert [path.name for path in written] == ["0002_retire_old_name.py"]
        assert guard.changes(graph=loader.graph) == {}
    else:
        assert written == ()
        with pytest.raises(
            CommandError, match=r"resources\.legacy\.old_name: column resources_legacy\.old_name holds data in 1 row",
        ):
            guard.changes(graph=loader.graph)


def test_applicable_declarations_are_planned_sequentially(runtime_migration_probe) -> None:
    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(
        source_root / "runtime_migrations" / "add_marker.py",
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
    write_addon_manifest(
        addon,
        migrations=(
            dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),
            dict(name="add_marker", app_label="resources", module="runtime_migrations.add_marker"),
        ),
    )
    importlib.invalidate_caches()

    written = materializer.materialize(apps=apps)

    assert [path.name for path in written] == ["0002_rename_legacy.py", "0003_add_marker.py"]
    second = (runtime_dir / "resources" / "migrations" / "0003_add_marker.py").read_text(encoding="utf-8")
    assert 'Migration.dependencies.append(("resources", "0002_rename_legacy"))' in second


def test_deferred_declaration_is_retried_in_same_materialization(runtime_migration_probe) -> None:
    """A guard enabled by a later declaration does not require another build."""

    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(
        source_root / "runtime_migrations" / "add_marker.py",
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
    write_addon_manifest(
        addon,
        migrations=(
            dict(name="add_marker", app_label="resources", module="runtime_migrations.add_marker"),
            dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),
        ),
    )
    importlib.invalidate_caches()

    written = materializer.materialize(apps=apps)

    assert [path.name for path in written] == ["0002_rename_legacy.py", "0003_add_marker.py"]
    assert materializer.materialize(apps=apps) == ()
    state = MigrationLoader(None, ignore_no_migrations=True).project_state()
    assert "marker" in state.models["resources", "legacy"].fields


@pytest.mark.parametrize("dependent_first", [True, False])
def test_deferred_cross_app_declaration_depends_on_present_planned_leaf(
    runtime_migration_probe, monkeypatch, settings, dependent_first: bool
) -> None:
    """A later-round declaration receives a concrete native cross-app dependency."""

    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    compose_migrations = runtime_dir / "compose" / "migrations"
    _write_module(runtime_dir / "compose" / "__init__.py")
    _write_module(compose_migrations / "__init__.py")
    _write_module(
        source_root / "runtime_migrations" / "after_flag.py",
        """from django.db import migrations
def applies(project_state):
    return ("compose", "flag") in project_state.models
class Migration(migrations.Migration):
    dependencies = []
    operations = []
""",
    )
    _write_module(
        source_root / "runtime_migrations" / "add_flag.py",
        """from django.db import migrations, models
def applies(project_state):
    return ("compose", "flag") not in project_state.models
class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.CreateModel(name="Flag", fields=[("id", models.AutoField(primary_key=True))])]
""",
    )
    dependent = dict(name="after_flag", app_label="resources", module="runtime_migrations.after_flag")
    enabling = dict(name="add_flag", app_label="compose", module="runtime_migrations.add_flag")
    write_addon_manifest(addon, migrations=(dependent, enabling) if dependent_first else (enabling, dependent))
    monkeypatch.setitem(settings.MIGRATION_MODULES, "compose", f"{runtime_dir.name}.compose.migrations")
    importlib.invalidate_caches()
    materializer = RuntimeMigrations((addon,), runtime_dir=runtime_dir, labels=("resources", "compose"))

    written = materializer.materialize(apps=apps)

    assert [path.name for path in written] == ["0001_add_flag.py", "0002_after_flag.py"]
    deferred = (runtime_dir / "resources" / "migrations" / "0002_after_flag.py").read_text()
    assert 'Migration.dependencies.append(("compose", "0001_add_flag"))' in deferred
    assert materializer.materialize(apps=apps) == ()


def test_latest_dependency_resolves_to_other_runtime_leaf(runtime_migration_probe, monkeypatch, settings) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    compose_migrations = runtime_dir / "compose" / "migrations"
    _write_module(runtime_dir / "compose" / "__init__.py")
    _write_module(compose_migrations / "__init__.py")
    _write_module(
        compose_migrations / "0004_current.py",
        """\
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = []
    operations = []
""",
    )
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            "dependencies = []", 'dependencies = [("compose", "__latest__")]', 1
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(settings.MIGRATION_MODULES, "compose", f"{runtime_dir.name}.compose.migrations")
    importlib.invalidate_caches()

    materializer.materialize(apps=apps)

    text = (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").read_text(encoding="utf-8")
    assert '("compose", "0004_current") if dependency == ("compose", "__latest__")' in text


def test_materialization_is_idempotent(runtime_migration_probe) -> None:
    materializer, _, _, _, _ = runtime_migration_probe

    first = materializer.materialize(apps=apps)
    second = materializer.materialize(apps=apps)

    assert len(first) == 1
    assert second == ()


def test_removed_declaration_preserves_materialized_body_and_graph(runtime_migration_probe) -> None:
    materializer, addon, source_path, _, _ = runtime_migration_probe
    (output,) = materializer.materialize(apps=apps)
    body = output.read_bytes()
    previous_graph = MigrationLoader(None, ignore_no_migrations=True).graph

    write_addon_manifest(addon)
    source_path.unlink()

    assert materializer.materialize(apps=apps) == ()
    materializer.check()

    assert output.read_bytes() == body
    graph = MigrationLoader(None, ignore_no_migrations=True).graph
    assert graph.nodes.keys() == previous_graph.nodes.keys()
    for node in graph.nodes:
        assert graph.forwards_plan(node) == previous_graph.forwards_plan(node)


@pytest.fixture
def released_baseline_migration(runtime_migration_probe):
    materializer, addon, source_path, runtime_dir, _ = runtime_migration_probe
    output = runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py"
    # Literal output of the retired fresh-history writer, independent of the
    # compatibility renderer used to validate it.
    output.write_text(
        '''\
"""Fresh-history baseline for one released addon migration."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("resources", "0001_legacy")
    ]
    operations = []
# ANGEE MATERIALIZED MIGRATION - DO NOT EDIT
Migration.angee_origin = "example.demo:rename_legacy"
'''
        f'Migration.angee_source_sha256 = "{hashlib.sha256(source_path.read_bytes()).hexdigest()}"\n'
        "Migration.angee_fresh_baseline = True\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    return materializer, addon, source_path, output


@pytest.mark.parametrize("retained_declaration", [True, False])
def test_released_fresh_baseline_is_loaded_without_rewriting(released_baseline_migration, retained_declaration) -> None:
    materializer, addon, source_path, output = released_baseline_migration
    body = output.read_bytes()
    if not retained_declaration:
        write_addon_manifest(addon)
        source_path.unlink()

    assert materializer.materialize(apps=apps) == ()
    materializer.check()

    assert output.read_bytes() == body
    loader = MigrationLoader(None, ignore_no_migrations=True)
    migration = loader.disk_migrations["resources", "0002_rename_legacy"]
    assert migration.dependencies == [("resources", "0001_legacy")]
    assert migration.operations == []


@pytest.mark.parametrize("alteration", [
    "    operations = [migrations.RunPython(migrations.RunPython.noop)]\n",
    "    operations = []  # edited\n",
])
def test_released_fresh_baseline_body_is_frozen(released_baseline_migration, alteration: str) -> None:
    materializer, _, _, output = released_baseline_migration
    output.write_text(output.read_text(encoding="utf-8").replace("    operations = []\n", alteration), encoding="utf-8")

    with pytest.raises(RuntimeError, match="materialized body digest changed"):
        materializer.materialize(apps=apps)


def test_check_reports_pending_without_writing(runtime_migration_probe) -> None:
    materializer, _, _, runtime_dir, _ = runtime_migration_probe

    with pytest.raises(
        RuntimeError,
        match="pending addon runtime migration example.demo:rename_legacy",
    ):
        materializer.check()

    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def test_changed_released_source_fails_instead_of_rewriting(runtime_migration_probe) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    materializer.materialize(apps=apps)
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "# changed\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="source digest changed"):
        materializer.materialize(apps=apps)


def test_declared_compatible_released_source_remains_immutable(runtime_migration_probe) -> None:
    """An exact historical source digest may coexist with its current declaration."""

    materializer, addon, source_path, _, _ = runtime_migration_probe
    (output,) = materializer.materialize(apps=apps)
    released_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source_path.write_text(source_path.read_text(encoding="utf-8") + "# current source\n", encoding="utf-8")
    write_addon_manifest(
        addon,
        migrations=(
            dict(
                name="rename_legacy",
                app_label="resources",
                module="runtime_migrations.rename_legacy",
                compatible_source_sha256=[released_digest],
            ),
        ),
    )

    assert materializer.materialize(apps=apps) == ()
    output.write_text(
        output.read_text(encoding="utf-8").replace("def forwards", "def edited_forwards", 1),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="materialized body digest changed"):
        materializer.materialize(apps=apps)


@pytest.mark.parametrize("digest", ["not-a-digest", "A" * 64, 7])
def test_compatible_source_digest_requires_exact_lowercase_sha256(
    runtime_migration_probe,
    digest: object,
) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    write_addon_manifest(
        addon,
        migrations=(
            dict(
                name="rename_legacy",
                app_label="resources",
                module="runtime_migrations.rename_legacy",
                compatible_source_sha256=[digest],
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="compatible_source_sha256"):
        materializer.materialize(apps=apps)


def test_changed_materialized_body_fails_instead_of_becoming_history(runtime_migration_probe) -> None:
    materializer, _, _, runtime_dir, _ = runtime_migration_probe
    (output,) = materializer.materialize(apps=apps)
    output.write_text(
        output.read_text(encoding="utf-8").replace("def forwards", "def edited_forwards", 1),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="materialized body digest changed"):
        materializer.materialize(apps=apps)

    assert output == runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py"


@pytest.mark.parametrize(
    "declaration, message",
    [
        (
            dict(name="Bad-Name", app_label="resources", module="runtime_migrations.rename_legacy"),
            "migration name must be a lower-case Python identifier",
        ),
        (
            dict(name="rename_legacy", app_label="unknown", module="runtime_migrations.rename_legacy"),
            "unknown runtime migration target 'unknown'",
        ),
    ],
)
def test_rejects_invalid_declarations(runtime_migration_probe, declaration, message: str) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    write_addon_manifest(addon, migrations=(declaration,))

    with pytest.raises(RuntimeError, match=message):
        materializer.materialize(apps=apps)


def test_rejects_duplicate_declared_origins(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    declaration = dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy")
    write_addon_manifest(addon, migrations=(declaration, declaration))

    with pytest.raises(RuntimeError, match="duplicate addon runtime migration origin example.demo:rename_legacy"):
        materializer.materialize(apps=apps)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("class Migration", "class NotMigration", "must define a Django Migration class"),
        ("def applies", "def not_applies", "must define applies"),
        (
            'return model is not None and "old_name" in model.fields',
            'return "yes"',
            "must return bool",
        ),
        (
            "    dependencies = []",
            '    dependencies = []\n    replaces = [("resources", "0001_legacy")] ',
            "cannot replace other migrations",
        ),
    ],
)
def test_rejects_invalid_source_contract(runtime_migration_probe, old: str, new: str, message: str) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )
    importlib.invalidate_caches()

    with pytest.raises(RuntimeError, match=message):
        materializer.materialize(apps=apps)


def test_rejects_unresolved_latest_dependency(runtime_migration_probe) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            "dependencies = []", 'dependencies = [("missing", "__latest__")]', 1
        ),
        encoding="utf-8",
    )
    importlib.invalidate_caches()

    with pytest.raises(RuntimeError, match="__latest__ dependency app 'missing' has no migration leaf"):
        materializer.materialize(apps=apps)


def test_rejects_multiple_target_leaves(runtime_migration_probe) -> None:
    materializer, _, _, runtime_dir, _ = runtime_migration_probe
    migrations_dir = runtime_dir / "resources" / "migrations"
    for name in ("0002_left", "0002_right"):
        _write_module(
            migrations_dir / f"{name}.py",
            """\
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("resources", "0001_legacy")]
    operations = []
""",
        )
    importlib.invalidate_caches()

    with pytest.raises(
        RuntimeError,
        match="example.demo:rename_legacy: runtime migration target 'resources' has multiple leaves",
    ):
        materializer.materialize(apps=apps)


def test_rejects_duplicate_materialized_origins(runtime_migration_probe) -> None:
    materializer, _, _, runtime_dir, _ = runtime_migration_probe
    (output,) = materializer.materialize(apps=apps)
    duplicate = runtime_dir / "resources" / "migrations" / "0003_duplicate.py"
    duplicate.write_text(output.read_text(encoding="utf-8"), encoding="utf-8")
    importlib.invalidate_caches()

    with pytest.raises(
        RuntimeError,
        match="duplicate materialized addon runtime migration origin example.demo:rename_legacy",
    ):
        materializer.materialize(apps=apps)


def test_rejects_run_before_cycle(runtime_migration_probe) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            "    dependencies = []",
            '    dependencies = []\n    run_before = [("resources", "0001_legacy")] ',
            1,
        ),
        encoding="utf-8",
    )
    importlib.invalidate_caches()

    with pytest.raises(RuntimeError, match="migration graph is invalid"):
        materializer.materialize(apps=apps)


def test_invalid_later_declaration_writes_no_earlier_plan(runtime_migration_probe) -> None:
    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(
        source_root / "runtime_migrations" / "broken.py",
        """\
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = []
    operations = []
""",
    )
    write_addon_manifest(
        addon,
        migrations=(
            dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),
            dict(name="broken", app_label="resources", module="runtime_migrations.broken"),
        ),
    )
    importlib.invalidate_caches()

    with pytest.raises(RuntimeError, match="example.demo:broken: source module must define applies"):
        materializer.materialize(apps=apps)

    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def test_rejects_source_in_djangos_conventional_migrations_package(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    write_addon_manifest(
        addon, migrations=(dict(name="rename_legacy", app_label="resources", module="migrations.rename_legacy"),)
    )

    with pytest.raises(RuntimeError, match="must live outside Django's conventional migrations package"):
        materializer.materialize(apps=apps)


def test_applies_error_is_reported_with_the_declaration_origin(runtime_migration_probe) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            'model = project_state.models.get(("resources", "legacy"))',
            'raise ValueError("broken guard")',
            1,
        ),
        encoding="utf-8",
    )
    importlib.invalidate_caches()

    with pytest.raises(
        RuntimeError,
        match=r"example.demo:rename_legacy: applies\(project_state\) failed",
    ):
        materializer.materialize(apps=apps)


def test_later_render_error_writes_no_earlier_plan(runtime_migration_probe) -> None:
    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(
        source_root / "runtime_migrations" / "add_marker.py",
        """\
from django.db import migrations, models


def applies(project_state):
    model = project_state.models.get(("resources", "legacy"))
    return model is not None and "new_name" in model.fields


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.AddField(
            model_name="legacy",
            name="marker",
            field=models.BooleanField(default=False),
        ),
    ]
""".rstrip("\n"),
    )
    write_addon_manifest(
        addon,
        migrations=(
            dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),
            dict(name="add_marker", app_label="resources", module="runtime_migrations.add_marker"),
        ),
    )
    importlib.invalidate_caches()

    with pytest.raises(RuntimeError, match="source migration must end with a newline"):
        materializer.materialize(apps=apps)

    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def test_materialized_origin_uses_app_config_name(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    write_addon_manifest(
        addon,
        migrations=(dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),),
    )

    (output,) = materializer.materialize(apps=apps)

    assert 'Migration.angee_origin = "example.demo:rename_legacy"' in output.read_text(encoding="utf-8")


def test_rejects_malformed_dependency_with_origin(runtime_migration_probe) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace("dependencies = []", "dependencies = [3]", 1),
        encoding="utf-8",
    )
    importlib.invalidate_caches()

    with pytest.raises(
        RuntimeError,
        match="example.demo:rename_legacy: invalid Django migration dependency 3",
    ):
        materializer.materialize(apps=apps)


@pytest.mark.parametrize(
    "declaration, message",
    [
        ({"name": "bad"}, "requires string app_label"),
        ({"name": 3, "app_label": "resources", "module": "runtime_migrations.rename_legacy"}, "requires string name"),
    ],
)
def test_migration_owner_validates_native_manifest_fields(runtime_migration_probe, declaration, message) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    write_addon_manifest(addon, migrations=[declaration])
    with pytest.raises(RuntimeError, match=message):
        materializer.plan()


@pytest.mark.parametrize("node", ["ab", ("resources", "0001_legacy", "extra"), ("resources",)])
def test_run_before_rejects_non_pair_nodes(node) -> None:
    with pytest.raises(RuntimeError, match="invalid Django run_before node"):
        RuntimeMigrations._resolve_run_before(None, [node], current_app="resources", origin="example:probe")


def test_validated_plan_render_uses_the_hashed_source_snapshot(runtime_migration_probe) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    (plan,) = materializer.plan()
    expected = source_path.read_text(encoding="utf-8")
    source_path.write_text("changed after planning\n", encoding="utf-8")
    rendered = materializer._render(plan)
    assert rendered.startswith(expected)
    assert not rendered.startswith("changed after planning")


def _upgrade_states(label):
    """Freeze affected field/constraint shapes from the 0a55a6fb upgrade floor.

    Legacy declarations use that revision's field classes, choices and defaults,
    independently of live models. Unrelated columns are omitted; relation targets
    outside the affected tables use scalar stand-ins for this isolated database.
    """
    declarations = ((Folder, ("smart_kind",), ("uniq_storage_folder_owner_smart_kind",)),)
    extras = {"folder": {"owner": models.IntegerField(null=True), "is_virtual": models.BooleanField(default=False)}}
    legacy_fields = {"folder": {"smart_kind": StateField(
        choices=[("trash", "Trash")], blank=True, default="", editable=False,
    )}}
    legacy_constraints = {"folder": [models.UniqueConstraint(
        fields=("owner", "smart_kind"), condition=models.Q(is_virtual=True) & ~models.Q(smart_kind=""),
        name="uniq_storage_folder_owner_smart_kind",
    )]}

    old = ProjectState()
    current = ProjectState()
    for model, names, constraints in declarations:
        name = model._meta.model_name
        fields = {"id": models.AutoField(primary_key=True), **extras.get(name, {})}
        old_fields = {key: field.clone() for key, field in fields.items()}
        for field_name in names:
            field = model._meta.get_field(field_name)
            fields[field_name] = field.clone()
            old_fields[field_name] = legacy_fields[name][field_name]
        old.add_model(ModelState(label, model.__name__, old_fields, options={
            "constraints": legacy_constraints.get(name, []),
        }))
        current.add_model(ModelState(label, model.__name__, fields, options={
            "constraints": [
                constraint.clone() for constraint in model._meta.constraints if constraint.name in constraints
            ],
        }))
    return old, current


@contextmanager
def _default_database(name: str, wrapper: type[BaseDatabaseWrapper] = DatabaseWrapper) -> Iterator[None]:
    """Swap the default alias to a separate SQLite (or dummy) database."""
    original = connections["default"]
    database = wrapper({
        **original.settings_dict,
        "ENGINE": wrapper.__module__.rpartition(".")[0],
        "NAME": name,
        "OPTIONS": {},
        "TIME_ZONE": None,
    }, alias="default")
    connections["default"] = database
    try:
        yield
    finally:
        database.close()
        connections["default"] = original


@pytest.fixture
def isolated_upgrade_database():
    """Replay real constraint names without colliding with the source test apps."""
    with _default_database(":memory:"):
        yield


@pytest.mark.parametrize("label,schema_nullable", [
    ("storage", False), ("storage", True),
])
@pytest.mark.django_db(transaction=True)
def test_declared_upgrades_preserve_floor_rows_or_reject_partial_schema(
    runtime_migration_probe, settings, monkeypatch, isolated_upgrade_database, label, schema_nullable,
):
    """Replay retained declarations and preserve floor rows without live source imports."""
    _, _, _, runtime_dir, _ = runtime_migration_probe
    legacy, current = _upgrade_states(label)
    if schema_nullable:
        for key, model in legacy.models.items():
            for name, field in model.fields.items():
                if current.models[key].fields[name].null:
                    field.null = True
    package = runtime_dir / label / "migrations"
    _write_module(package.parent / "__init__.py")
    _write_module(package / "__init__.py")
    initial = migrations.Migration("0001_legacy", label)
    initial.operations = [
        migrations.CreateModel(model.name, list(model.fields.items()), options=model.options)
        for model in legacy.models.values()
    ]
    _write_module(package / "0001_legacy.py", MigrationWriter(initial).as_string())
    monkeypatch.setitem(settings.MIGRATION_MODULES, label, f"{runtime_dir.name}.{label}.migrations")
    importlib.invalidate_caches()
    addon = apps.get_app_config(label)
    materializer = RuntimeMigrations((addon,), runtime_dir=runtime_dir, labels=(label,))
    if schema_nullable:
        # Recompiling a legacy '' constraint through an already-nullable
        # StateField changes its meaning. Reject this partial graph before writes.
        with pytest.raises(RuntimeError, match=r"applies\(project_state\) failed") as caught:
            materializer.materialize(apps=apps)
        assert isinstance(caught.value.__cause__, ValueError)
        assert "partial nullable transition" in str(caught.value.__cause__)
        assert sorted(path.name for path in package.glob("[0-9]*.py")) == ["0001_legacy.py"]
        return
    (plan,) = materializer.plan()
    source = importlib.import_module(f"angee.{label}.runtime_migrations.{plan.origin.split(':')[1]}")
    assert not source.applies(ProjectState())
    assert not source.applies(current)
    assert materializer.materialize(apps=apps) == (plan.output_path,)
    assert materializer.materialize(apps=apps) == ()

    loader = MigrationLoader(None, ignore_no_migrations=True)
    before = loader.project_state([(label, "0001_legacy")])
    after = loader.project_state([(label, plan.name)])
    expected = before.clone()
    for key, model in current.models.items():
        expected.models[key] = model.clone()
        for name, field in model.fields.items():
            assert after.models[key].fields[name].deconstruct()[1:] == field.deconstruct()[1:], (key, name)
    questioner = Mock(spec=MigrationQuestioner)
    questioner.ask_rename.side_effect = AssertionError("unexpected rename prompt")
    questioner.ask_not_null_addition.side_effect = AssertionError("unexpected default prompt")
    assert MigrationAutodetector(after, expected, questioner).changes(graph=loader.graph) == {}
    assert not questioner.mock_calls

    with connection.schema_editor() as editor:
        for key in legacy.models:
            editor.create_model(before.apps.get_model(*key))
    try:
        folder = before.apps.get_model(label, "Folder")
        folder._base_manager.create(owner=1, is_virtual=True)
        folder._base_manager.create(owner=1, is_virtual=True)
        folder._base_manager.create(owner=1, is_virtual=True, smart_kind="trash")

        def snapshot(state):
            # Raw SQL verifies storage without modern StateField coercing '' to None.
            with connection.cursor() as cursor:
                result = {}
                for key in legacy.models:
                    table = state.apps.get_model(*key)._meta.db_table
                    cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)} ORDER BY id")
                    names = [column[0] for column in cursor.description]
                    result[key] = [dict(zip(names, row)) for row in cursor.fetchall()]
                return result

        original = snapshot(before)
        migration = loader.disk_migrations[label, plan.name]
        with connection.schema_editor() as editor:
            migration.apply(before, editor)
            source.forwards(after.apps, editor)  # Data conversion is idempotent.
        upgraded = snapshot(after)
        assert upgraded == {
            key: [{name: None if value == "" and name in current.models[key].fields
                   and current.models[key].fields[name].null else value for name, value in row.items()}
                  for row in rows]
            for key, rows in original.items()
        }
        with connection.schema_editor() as editor:
            migration.unapply(
                loader.project_state([(label, "0001_legacy")]), editor,
            )
        assert snapshot(before) == original
    finally:
        with connection.schema_editor() as editor:
            for key in reversed(legacy.models):
                editor.delete_model(before.apps.get_model(*key))


@pytest.mark.parametrize("label,name", [
    ("storage", "smart_kind_nullable"),
])
@pytest.mark.parametrize("change", ["condition", "name", "remove"])
def test_optional_state_upgrade_skips_evolved_current_constraints(label, name, change):
    source = importlib.import_module(f"angee.{label}.runtime_migrations.{name}")
    _, current = _upgrade_states(label)
    for key, model in current.models.items():
        for index in range(len(model.options.get("constraints", []))):
            evolved = current.clone()
            constraints = [constraint.clone() for constraint in model.options["constraints"]]
            evolved.models[key].options["constraints"] = constraints
            if change == "condition":
                constraints[index].condition &= models.Q(pk__gt=0)
            elif change == "name":
                constraints[index].name += "_revised"
            else:
                del constraints[index]
            assert not source.applies(evolved), (key, index, change)
