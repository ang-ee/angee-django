"""Addon-owned migrations materialized into Django runtime graphs."""

from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.db import connection, connections, migrations, models
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.dummy.base import DatabaseWrapper as DummyDatabaseWrapper
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ModelState, ProjectState
from django.db.models.functions import Lower
from django.test import override_settings
from django.utils import timezone

from angee.addons import addon_manifest
from angee.base.fields.identity import SqidField
from angee.compose.migrations import DropGuardAutodetector, RuntimeMigrations
from angee.resources.entries import resource_manifest_for, retired_models_for
from tests.composed_host import COMPOSED_TEST_TIMEOUT
from tests.conftest import make_addon, write_addon_manifest


def _write_module(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@contextmanager
def _default_database(name: str, wrapper: type[BaseDatabaseWrapper] = DatabaseWrapper) -> Iterator[None]:
    """Swap the default alias to a separate SQLite (or dummy) database."""

    original = connections["default"]
    database = wrapper(
        {
            **original.settings_dict,
            "ENGINE": wrapper.__module__.rpartition(".")[0],
            "NAME": name,
            "OPTIONS": {},
            "TIME_ZONE": None,
        },
        alias="default",
    )
    connections["default"] = database
    try:
        yield
    finally:
        database.close()
        connections["default"] = original


@pytest.fixture
def isolated_runtime_database():
    """Exercise drop guards without colliding with the source test apps."""

    with _default_database(":memory:"):
        yield


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


def test_reset_migration_history_replaces_only_composed_history(tmp_path: Path) -> None:
    """Applied composed history becomes clean initials without changing any other app's records."""

    addon_dir = tmp_path / "addons"
    _write_module(addon_dir / "reset" / "__init__.py")
    for name, depends_on, body in (
        (
            "beta",
            ("django.contrib.auth",),
            """\
from django.db import models


class Target(models.Model):
    runtime = True
    name = models.CharField(max_length=40)

    class Meta:
        abstract = True
""",
        ),
        (
            "alpha",
            ("reset.beta",),
            """\
from django.db import models


class Source(models.Model):
    runtime = True
    name = models.CharField(max_length=80)
    target = models.ForeignKey("beta.Target", models.CASCADE)

    class Meta:
        abstract = True
""",
        ),
    ):
        package = addon_dir / "reset" / name
        _write_module(package / "__init__.py")
        _write_module(package / "models.py", body)
        dependencies = ", ".join(repr(dependency) for dependency in depends_on)
        _write_module(
            package / "addon.toml",
            f'[addon]\nname = "reset.{name}"\ndepends_on = [{dependencies}]\n',
        )

    _write_module(addon_dir / "reset" / "external" / "__init__.py")
    _write_module(addon_dir / "reset" / "external" / "migrations" / "__init__.py")
    _write_module(
        addon_dir / "reset" / "external" / "migrations" / "0001_initial.py",
        """\
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("beta", "__first__")]
    operations = []
""",
    )

    report = tmp_path / "reset-migrations.json"
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests/composed_host.py"),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--addon-dir",
            str(addon_dir),
            "--no-examples",
            "--app",
            "reset.alpha",
            "--app",
            "reset.external",
            "--action",
            "reset-migrations",
            "--output",
            str(report),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=COMPOSED_TEST_TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, f"composed reset failed:\n{result.stdout}\n{result.stderr}"
    outcome = json.loads(report.read_text(encoding="utf-8"))
    assert outcome["labels"] == ["alpha", "beta"]
    assert "manage.py migrate --database default" in outcome["pending_error"]
    assert "manage.py makemigrations alpha beta" in outcome["drift_error"]
    assert "manage.py migrate --database default" in outcome["drift_error"]
    assert outcome["files"] == {"alpha": ["0001_initial.py"], "beta": ["0001_initial.py"]}
    assert outcome["old_rows"] == [
        ["alpha", "0001_previous"],
        ["alpha", "0002_history_probe"],
        ["beta", "0001_previous"],
    ]
    assert outcome["new_rows"] == [["alpha", "0001_initial"], ["beta", "0001_initial"]]
    assert outcome["noncomposed_unchanged"] is True
    assert outcome["external_dependency_applied"] is True
    assert outcome["failure_rollback"] == {"generation": True, "recording": True}
    assert outcome["replay_unchanged"] is True
    assert outcome["plan_empty"] is outcome["changes_empty"] is outcome["runtime_check"] is True
    assert outcome["declarations"] == 0
    assert ["beta", "0001_initial"] in outcome["alpha_dependencies"]
    assert outcome["command_output"] == [
        "labels: alpha, beta",
        "files removed: 3",
        "migrations recorded: 2",
    ]


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
@pytest.mark.usefixtures("isolated_runtime_database")
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
    assert "cutover migration" in message


@pytest.mark.parametrize("rows", [0, 1])
@pytest.mark.django_db
@pytest.mark.usefixtures("isolated_runtime_database")
def test_autodetected_table_drop_requires_an_empty_table(rows: int) -> None:
    """A model removed from code drops its table automatically only while the table holds no row."""

    history, current = ProjectState(), ProjectState()
    for state in (history, current):
        state.add_model(ModelState("probe", "Kept", [("id", models.AutoField(primary_key=True))]))
    history.add_model(ModelState("probe", "Retired", [("id", models.AutoField(primary_key=True))]))
    graph = MigrationGraph()
    graph.add_node(("probe", "0001_initial"), migrations.Migration("0001_initial", "probe"))
    retired = history.apps.get_model("probe", "Retired")
    with connection.schema_editor() as editor:
        editor.create_model(retired)
    retired._base_manager.bulk_create([retired() for _ in range(rows)])

    guard = DropGuardAutodetector(history, current)
    if not rows:
        (migration,) = guard.changes(graph=graph)["probe"]
        assert [type(operation).__name__ for operation in migration.operations] == ["DeleteModel"]
        return
    with pytest.raises(CommandError) as refused:
        guard.changes(graph=graph)
    message = str(refused.value)
    assert message.startswith(
        "unsafe autodetected table drop: probe.retired: table probe_retired holds data in 1 row(s) on database "
        "'default'. "
    )
    assert "cutover migration" in message


def seed_ledger(model_label: str, public_id: str, *, addon: str = "probe", xref: str = "seeded") -> None:
    """Record a resource-ledger row naming ``model_label``, as its addon's resource files once seeded it.

    The ledger's label field refuses models no longer in code, so the row is
    written raw, as it survives in a database whose model was since removed.
    """

    ledger = apps.get_model("resources", "Resource")
    if ledger._meta.db_table not in connection.introspection.table_names():
        with connection.schema_editor() as editor:
            editor.create_model(ledger)
    values = {
        "source_addon": addon, "source_path": f"resources/master/010_{xref}.yaml", "tier": "master",
        "xref": xref, "content_hash": "sha256:" + "0" * 64, "target_model": model_label,
        "target_id": public_id, "loaded_at": timezone.now(),
    }
    fields = [field for field in ledger._meta.local_concrete_fields if not field.primary_key]
    row = [
        values[field.attname] if field.attname in values
        else timezone.now() if isinstance(field, models.DateTimeField) and not field.null
        else field.get_default() if field.has_default() else None
        for field in fields
    ]
    columns = ", ".join(connection.ops.quote_name(field.column) for field in fields)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {connection.ops.quote_name(ledger._meta.db_table)} ({columns}) "
            f"VALUES ({', '.join(['%s'] * len(fields))})",
            [field.get_db_prep_value(value, connection) if field.attname not in ("target_model",) else value
             for field, value in zip(fields, row, strict=True)],
        )


@pytest.mark.parametrize("public_ids", ["sqid", "primary_key"])
@pytest.mark.django_db
@pytest.mark.usefixtures("isolated_runtime_database")
def test_autodetected_table_drop_lets_rows_its_retiring_addon_seeded_go(monkeypatch, public_ids: str) -> None:
    """Only the addon that names the model vouches for its own seeded rows; any other row refuses the drop."""

    history, current = ProjectState(), ProjectState()
    for state in (history, current):
        state.add_model(ModelState("probe", "Kept", [("id", models.AutoField(primary_key=True))]))
    history.add_model(ModelState("probe", "Retired", [("id", models.AutoField(primary_key=True))]))
    graph = MigrationGraph()
    graph.add_node(("probe", "0001_initial"), migrations.Migration("0001_initial", "probe"))
    retired = history.apps.get_model("probe", "Retired")
    with connection.schema_editor() as editor:
        editor.create_model(retired)
    codec = SqidField(real_field_name="id", min_length=8, prefix="ret")

    def public_id(row):
        return codec.public_id_from_value(row.pk) if public_ids == "sqid" else str(row.pk)

    owner = apps.get_app_config("resources")
    seeded = retired._base_manager.create()
    seed_ledger("probe.Retired", public_id(seeded), addon=owner.name)
    refused = r"probe\.retired: table probe_retired holds data in 1 row\(s\)"
    with pytest.raises(CommandError, match=refused):  # No addon vouches for the row yet.
        DropGuardAutodetector(history, current).changes(graph=graph)

    monkeypatch.setitem(addon_manifest(owner).resources, "retired_models", ["probe.Retired"])
    (migration,) = DropGuardAutodetector(history, current).changes(graph=graph)["probe"]
    assert [type(operation).__name__ for operation in migration.operations] == ["DeleteModel"]

    # The compiler by itself grants no resource exemption. Removing the owner
    # leaves even this vouched-for row protected by the ordinary populated guard.
    with override_settings(COMPOSE_INTEGRATIONS=[]):
        with pytest.raises(CommandError, match=refused):
            DropGuardAutodetector(history, current).changes(graph=graph)

    # Another addon's seeded row, a ledger id that is not the row's canonical public
    # id, and an unledgered row each still count as data.
    for addon, ledgered in (("probe.other", True), (owner.name, False), (None, None)):
        row = retired._base_manager.create()
        if addon is not None:
            stale = SqidField(real_field_name="id", min_length=0, prefix="ret").public_id_from_value(row.pk)
            seed_ledger("probe.Retired", public_id(row) if ledgered else stale, addon=addon, xref=f"row{row.pk}")
        with pytest.raises(CommandError, match=refused):
            DropGuardAutodetector(history, current).changes(graph=graph)
        retired._base_manager.filter(pk=row.pk).delete()


def test_retired_models_are_a_resources_declaration_not_a_tier(monkeypatch) -> None:
    messaging = apps.get_app_config("messaging")
    assert retired_models_for(messaging) == ("messaging.ActivityType",)
    assert "retired_models" not in resource_manifest_for(messaging)
    monkeypatch.setitem(addon_manifest(messaging).resources, "retired_models", "messaging.ActivityType")
    with pytest.raises(ImproperlyConfigured, match="retired_models must be an array of model labels"):
        retired_models_for(messaging)


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
@pytest.mark.usefixtures("isolated_runtime_database")
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


def test_check_reports_pending_without_writing(runtime_migration_probe) -> None:
    materializer, _, _, runtime_dir, _ = runtime_migration_probe

    with pytest.raises(
        RuntimeError,
        match="pending addon runtime migration example.demo:rename_legacy",
    ):
        materializer.check()

    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def test_changed_materialized_source_fails_instead_of_rewriting(runtime_migration_probe) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    materializer.materialize(apps=apps)
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "# changed\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="source digest changed"):
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
