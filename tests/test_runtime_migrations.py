"""Addon-owned migrations materialized into Django runtime graphs."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.core.management.commands.makemigrations import Command as NativeMakeMigrationsCommand
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
from angee.compose.management.commands.makemigrations import Command as ComposedMakeMigrationsCommand
from angee.compose.migrations import DropGuardAutodetector, RuntimeMigrations
from angee.compose.runtime import Runtime
from angee.management.commands.makemigrations import Command as HostMakeMigrationsCommand
from angee.resources.entries import resource_manifest_for, retired_models_for
from tests.composed_host import COMPOSED_TEST_TIMEOUT
from tests.conftest import make_addon, write_addon_manifest


@pytest.fixture(autouse=True)
def manifest_migration_declarations(settings):
    """These addon transition fixtures select the host's manifest adapter."""

    settings.COMPOSE_APP_DECLARATIONS = "angee.build.ManifestDeclarations"


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
        labels=("resources",),
    )
    return materializer, addon, source_path, runtime_dir, source_root


def test_retiring_the_last_model_preserves_the_declared_migration_app(runtime_migration_probe, settings) -> None:
    """A declared transition owns an empty app without a graphless scheduler."""

    _, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(source_root / "runtime_migrations" / "retire_last.py", """\
from django.db import migrations


def applies(state):
    return ("demo", "retired") in state.models


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.DeleteModel(name="Retired")]
""")
    write_addon_manifest(addon, migrations=({
        "name": "retire_last", "app_label": addon.label, "module": "runtime_migrations.retire_last",
    },))
    settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, addon.label: f"{addon.name}.migrations"}
    runtime = Runtime.discover((addon,), runtime_dir=runtime_dir, integrations=())
    assert runtime.labels == (addon.label,)
    sources = runtime.render_sources()
    native_history = f"RUNTIME_MIGRATION_MODULES = {{{addon.label!r}: '{addon.name}.migrations'}}"
    assert native_history in sources[Path("__init__.py")]
    assert "Retired" not in sources[Path(addon.label, "models.py")]
    state = ProjectState()
    state.add_model(ModelState(addon.label, "Retired", [("id", models.AutoField(primary_key=True))]))
    source = importlib.import_module(f"{addon.name}.runtime_migrations.retire_last")
    assert source.applies(state) is True
    retired = source.Migration("retire_last", addon.label).mutate_state(state)
    assert (addon.label, "retired") not in retired.models
    assert source.applies(retired) is False


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


def test_materialize_copies_complete_source_and_freezes_current_leaf(runtime_migration_probe) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    initial = runtime_dir / "resources" / "migrations" / "0001_legacy.py"
    initial_bytes = initial.read_bytes()

    written = materializer.materialize(apps=apps)

    output = runtime_dir / "resources" / "migrations" / "rename_legacy.py"
    assert written == (output,)
    text = output.read_text(encoding="utf-8")
    assert text.startswith(source_path.read_text(encoding="utf-8"))
    assert "def forwards(apps, schema_editor):" in text
    assert "Migration.dependencies = [('resources', '0001_legacy')]" in text
    assert "Migration.run_before = []" in text
    assert 'Migration.angee_origin = "example.demo:rename_legacy"' in text
    assert initial.read_bytes() == initial_bytes

    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert loader.graph.leaf_nodes("resources") == [("resources", "rename_legacy")]
    state = loader.project_state()
    assert "new_name" in state.models["resources", "legacy"].fields
    assert "old_name" not in state.models["resources", "legacy"].fields


def test_false_pending_guard_refuses_without_writing(runtime_migration_probe) -> None:
    """A final-looking schema cannot prove that an addon data transition ran."""

    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            'return model is not None and "old_name" in model.fields', "return False"
        ),
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    migration_dir = runtime_dir / "resources" / "migrations"
    before = {path.name: path.read_bytes() for path in migration_dir.glob("*.py")}
    current_apps = MigrationLoader(None, ignore_no_migrations=True).project_state().apps

    with pytest.raises(RuntimeError, match="false schema guard cannot prove a data transition completed"):
        materializer.materialize(apps=current_apps)

    assert {path.name: path.read_bytes() for path in migration_dir.glob("*.py")} == before


@pytest.mark.parametrize("consumer_cutover", [False, True])
def test_adopted_table_has_an_explicit_native_cutover_chain(
    runtime_migration_probe, monkeypatch, settings, consumer_cutover: bool
) -> None:
    """Adopt, retarget consumers, then retire the old model; missing nodes never stage."""

    _, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(runtime_dir / "integrate_vcs" / "__init__.py")
    _write_module(runtime_dir / "integrate_vcs" / "migrations" / "__init__.py")
    monkeypatch.setitem(settings.MIGRATION_MODULES, "integrate_vcs", f"{runtime_dir.name}.integrate_vcs.migrations")
    _write_module(runtime_dir / "resources" / "migrations" / "0002_consumer.py", """\
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [("resources", "0001_legacy")]
    operations = [migrations.CreateModel(name="Consumer", fields=[
        ("id", models.AutoField(primary_key=True)),
        ("legacy", models.ForeignKey("resources.Legacy", on_delete=models.CASCADE)),
    ])]
""")
    _write_module(source_root / "adopt_legacy.py", """\
from django.db import migrations, models
def applies(state):
    return ("integrate_vcs", "legacy") not in state.models
class Migration(migrations.Migration):
    dependencies = [("resources", "0002_consumer")]
    operations = [migrations.SeparateDatabaseAndState(state_operations=[
        migrations.CreateModel(name="Legacy", fields=[
            ("id", models.AutoField(primary_key=True)),
            ("old_name", models.CharField(max_length=100)),
        ], options={"db_table": "resources_legacy"}),
    ])]
""")
    _write_module(source_root / "delete_legacy.py", """\
from django.db import migrations
def applies(state):
    return (
        ("resources", "legacy") in state.models
        and ("integrate_vcs", "legacy") in state.models
        and state.models["resources", "consumer"].fields["legacy"].remote_field.model.lower() == "integrate_vcs.legacy"
    )
class Migration(migrations.Migration):
    dependencies = [("resources", "consumer_cutover")]
    operations = [migrations.SeparateDatabaseAndState(state_operations=[migrations.DeleteModel(name="Legacy")])]
""")
    declarations = [
        dict(name="delete_legacy", app_label="resources", module="delete_legacy"),
        dict(name="adopt_legacy", app_label="integrate_vcs", module="adopt_legacy"),
    ]
    if consumer_cutover:
        _write_module(source_root / "consumer_cutover.py", """\
from django.db import migrations, models
def applies(state):
    return (
        ("integrate_vcs", "legacy") in state.models
        and state.models["resources", "consumer"].fields["legacy"].remote_field.model.lower() == "resources.legacy"
    )
class Migration(migrations.Migration):
    dependencies = [("integrate_vcs", "adopt_legacy")]
    operations = [migrations.SeparateDatabaseAndState(state_operations=[migrations.AlterField(
        model_name="consumer", name="legacy",
        field=models.ForeignKey("integrate_vcs.Legacy", on_delete=models.CASCADE),
    )])]
""")
        declarations.append(dict(name="consumer_cutover", app_label="resources", module="consumer_cutover"))
    write_addon_manifest(addon, migrations=tuple(declarations))
    importlib.invalidate_caches()
    materializer = RuntimeMigrations((addon,), labels=("resources", "integrate_vcs"))

    current = MigrationLoader(None, ignore_no_migrations=True).project_state()
    adopted = current.models["resources", "legacy"].clone()
    adopted.app_label = "integrate_vcs"
    adopted.options["db_table"] = "resources_legacy"
    current.add_model(adopted)
    current.alter_field(
        "resources", "consumer", "legacy",
        models.ForeignKey("integrate_vcs.Legacy", on_delete=models.CASCADE),
        preserve_default=True,
    )
    current.remove_model("resources", "legacy")
    before = {
        path.relative_to(runtime_dir): path.read_bytes()
        for path in runtime_dir.glob("*/migrations/*.py")
    }

    if consumer_cutover:
        written = materializer.materialize(apps=current.apps)
        assert [path.name for path in written] == [
            "adopt_legacy.py", "consumer_cutover.py", "delete_legacy.py",
        ]
        loader = MigrationLoader(None, ignore_no_migrations=True)
        assert ("integrate_vcs", "adopt_legacy") in loader.disk_migrations["resources", "consumer_cutover"].dependencies
        assert ("resources", "consumer_cutover") in loader.disk_migrations["resources", "delete_legacy"].dependencies
        assert materializer.materialize(apps=current.apps) == ()
        materializer.check()
    else:
        with pytest.raises(RuntimeError, match="migration graph is invalid.*consumer_cutover"):
            materializer.materialize(apps=current.apps)
        assert {
            path.relative_to(runtime_dir): path.read_bytes()
            for path in runtime_dir.glob("*/migrations/*.py")
        } == before


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
    audit = apps.get_model("rebac", "PermissionAuditEvent")
    existing = connection.introspection.table_names()
    with connection.schema_editor() as editor:
        for model in (ledger, audit):
            if model._meta.db_table not in existing:
                editor.create_model(model)
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

    materializer = RuntimeMigrations((addon,), labels=("resources",))
    written = materializer.materialize(apps=current.apps)
    loader = MigrationLoader(None, ignore_no_migrations=True)
    guard = DropGuardAutodetector(loader.project_state(), current)
    if declared:
        assert [path.name for path in written] == ["retire_old_name.py"]
        assert guard.changes(graph=loader.graph) == {}
    else:
        assert written == ()
        with pytest.raises(
            CommandError, match=r"resources\.legacy\.old_name: column resources_legacy\.old_name holds data in 1 row",
        ):
            guard.changes(graph=loader.graph)


@pytest.mark.parametrize("dependent_first", [False, True])
def test_native_dependencies_order_same_app_transitions(runtime_migration_probe, dependent_first: bool) -> None:
    """Manifest order never substitutes for the declared native dependency."""

    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(source_root / "runtime_migrations" / "add_marker.py", """\
from django.db import migrations, models
def applies(project_state):
    model = project_state.models.get(("resources", "legacy"))
    return model is not None and "new_name" in model.fields and "marker" not in model.fields
class Migration(migrations.Migration):
    dependencies = [("resources", "rename_legacy")]
    operations = [migrations.AddField(model_name="legacy", name="marker", field=models.BooleanField(default=False))]
""")
    parent = dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy")
    child = dict(name="add_marker", app_label="resources", module="runtime_migrations.add_marker")
    write_addon_manifest(addon, migrations=(child, parent) if dependent_first else (parent, child))
    importlib.invalidate_caches()

    written = materializer.materialize(apps=apps)

    assert [path.name for path in written] == ["rename_legacy.py", "add_marker.py"]
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert ("resources", "rename_legacy") in loader.disk_migrations["resources", "add_marker"].dependencies
    assert "marker" in loader.project_state().models["resources", "legacy"].fields


def test_missing_native_ordering_refuses_before_guards_or_writes(runtime_migration_probe) -> None:
    """Two pending leaves need an explicit edge, not guard retries or inferred rounds."""

    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(source_root / "runtime_migrations" / "add_marker.py", """\
from django.db import migrations
def applies(project_state):
    raise AssertionError("a conflicting graph must fail before any guard")
class Migration(migrations.Migration):
    dependencies = []
    operations = []
""")
    write_addon_manifest(addon, migrations=(
        dict(name="add_marker", app_label="resources", module="runtime_migrations.add_marker"),
        dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),
    ))
    importlib.invalidate_caches()
    migration_dir = runtime_dir / "resources" / "migrations"
    before = {path.name: path.read_bytes() for path in migration_dir.glob("*.py")}

    with pytest.raises(RuntimeError, match="multiple migration leaves"):
        materializer.materialize(apps=apps)

    assert {path.name: path.read_bytes() for path in migration_dir.glob("*.py")} == before


@pytest.mark.parametrize("dependent_first", [True, False])
def test_native_cross_app_prerequisite_state_precedes_guard(
    runtime_migration_probe, monkeypatch, settings, dependent_first: bool
) -> None:
    """Pending cross-app nodes participate in the graph before applicability runs."""

    _, addon, _, runtime_dir, source_root = runtime_migration_probe
    other_migrations = runtime_dir / "tests" / "migrations"
    _write_module(runtime_dir / "tests" / "__init__.py")
    _write_module(other_migrations / "__init__.py")
    _write_module(source_root / "runtime_migrations" / "after_flag.py", """\
from django.db import migrations
def applies(project_state):
    return ("tests", "flag") in project_state.models
class Migration(migrations.Migration):
    dependencies = [("tests", "add_flag")]
    operations = [migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop)]
""")
    _write_module(source_root / "runtime_migrations" / "add_flag.py", """\
from django.db import migrations, models
def applies(project_state):
    return ("tests", "flag") not in project_state.models
class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.CreateModel(name="Flag", fields=[("id", models.AutoField(primary_key=True))])]
""")
    dependent = dict(name="after_flag", app_label="resources", module="runtime_migrations.after_flag")
    prerequisite = dict(name="add_flag", app_label="tests", module="runtime_migrations.add_flag")
    write_addon_manifest(addon, migrations=(dependent, prerequisite) if dependent_first else (prerequisite, dependent))
    monkeypatch.setitem(settings.MIGRATION_MODULES, "tests", f"{runtime_dir.name}.tests.migrations")
    importlib.invalidate_caches()
    materializer = RuntimeMigrations((addon,), labels=("resources", "tests"))

    written = materializer.materialize(apps=apps)

    assert [path.name for path in written] == ["add_flag.py", "after_flag.py"]
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert ("tests", "add_flag") in loader.disk_migrations["resources", "after_flag"].dependencies
    assert materializer.materialize(apps=apps) == ()


@pytest.mark.parametrize("symbolic, resolved", [("__first__", "0001_start"), ("__latest__", "0004_current")])
def test_symbolic_dependency_is_frozen_to_its_native_node(
    runtime_migration_probe, monkeypatch, settings, symbolic: str, resolved: str,
) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    other_migrations = runtime_dir / "tests" / "migrations"
    _write_module(runtime_dir / "tests" / "__init__.py")
    _write_module(other_migrations / "__init__.py")
    for name, dependencies in (("0001_start", []), ("0004_current", [("tests", "0001_start")])):
        _write_module(other_migrations / f"{name}.py", f"""\
from django.db import migrations
class Migration(migrations.Migration):
    dependencies = {dependencies!r}
    operations = []
""")
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            "dependencies = []", f'dependencies = [("tests", "{symbolic}")]', 1,
        ), encoding="utf-8",
    )
    monkeypatch.setitem(settings.MIGRATION_MODULES, "tests", f"{runtime_dir.name}.tests.migrations")
    importlib.invalidate_caches()

    (output,) = materializer.materialize(apps=apps)

    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert loader.disk_migrations["resources", "rename_legacy"].dependencies == [
        ("tests", resolved), ("resources", "0001_legacy"),
    ]
    frozen = output.read_bytes()
    _write_module(other_migrations / "0005_later.py", """\
from django.db import migrations
class Migration(migrations.Migration):
    dependencies = [("tests", "0004_current")]
    operations = []
""")
    importlib.invalidate_caches()
    assert materializer.materialize(apps=apps) == ()
    assert output.read_bytes() == frozen
    assert MigrationLoader(None, ignore_no_migrations=True).disk_migrations[
        "resources", "rename_legacy"
    ].dependencies == [("tests", resolved), ("resources", "0001_legacy")]


def test_materialization_is_idempotent(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe

    first = materializer.materialize(apps=apps)
    source = importlib.import_module(f"{addon.name}.runtime_migrations.rename_legacy")
    assert source.applies(MigrationLoader(None, ignore_no_migrations=True).project_state()) is False
    frozen = first[0].read_bytes()
    second = materializer.materialize(apps=apps)

    assert len(first) == 1
    assert second == ()
    assert first[0].read_bytes() == frozen


def test_check_reports_pending_without_writing(runtime_migration_probe) -> None:
    materializer, _, _, runtime_dir, _ = runtime_migration_probe

    with pytest.raises(
        RuntimeError,
        match="pending addon runtime migration example.demo:rename_legacy",
    ):
        materializer.check()

    assert not (runtime_dir / "resources" / "migrations" / "rename_legacy.py").exists()


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

    assert output == runtime_dir / "resources" / "migrations" / "rename_legacy.py"


@pytest.mark.parametrize(
    "declaration, message",
    [
        (
            dict(name="Bad-Name", app_label="resources", module="runtime_migrations.rename_legacy"),
            "migration name must be a lower-case Python identifier",
        ),
        (
            dict(name="_hidden", app_label="resources", module="runtime_migrations.rename_legacy"),
            "without a leading underscore",
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

    with pytest.raises(RuntimeError, match=r"invalid dependency \('missing', '__latest__'\)"):
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
        match="multiple migration leaves.*resources",
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

    assert not (runtime_dir / "resources" / "migrations" / "rename_legacy.py").exists()


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
    dependencies = [("resources", "rename_legacy")]
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

    assert not (runtime_dir / "resources" / "migrations" / "rename_legacy.py").exists()


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
        match="example.demo:rename_legacy: invalid Django dependency 3",
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
    with pytest.raises(RuntimeError, match="example:probe: invalid Django run_before"):
        RuntimeMigrations._dependency_node(node, origin="example:probe", kind="run_before")


def test_validated_plan_render_uses_the_hashed_source_snapshot(runtime_migration_probe) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    (plan,) = materializer.plan()
    expected = source_path.read_text(encoding="utf-8")
    source_path.write_text("changed after planning\n", encoding="utf-8")
    rendered = materializer._render(plan)
    assert rendered.startswith(expected)
    assert not rendered.startswith("changed after planning")


def test_missing_pending_dependency_fails_before_guards_and_writes(runtime_migration_probe) -> None:
    """An absent native node cannot be inferred from a guard or an earlier staged file."""

    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(source_root / "runtime_migrations" / "after_missing.py", """\
from django.db import migrations

def applies(state):
    raise AssertionError("native graph validation must precede guard evaluation")

class Migration(migrations.Migration):
    dependencies = [("resources", "missing_cutover")]
    operations = []
""")
    write_addon_manifest(addon, migrations=(
        dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),
        dict(name="after_missing", app_label="resources", module="runtime_migrations.after_missing"),
    ))
    importlib.invalidate_caches()
    directory = runtime_dir / "resources" / "migrations"
    before = {path.name: path.read_bytes() for path in directory.glob("*.py")}

    with pytest.raises(RuntimeError, match="migration graph is invalid.*missing_cutover"):
        materializer.materialize(apps=apps)

    assert {path.name: path.read_bytes() for path in directory.glob("*.py")} == before


@pytest.mark.parametrize("dependent_first", [False, True])
def test_data_only_migrations_keep_their_explicit_cross_app_order(
    runtime_migration_probe, monkeypatch, settings, dependent_first: bool,
) -> None:
    """RunPython order is declared even when neither operation changes ProjectState."""

    _, addon, _, runtime_dir, source_root = runtime_migration_probe
    _write_module(runtime_dir / "tests" / "__init__.py")
    _write_module(runtime_dir / "tests" / "migrations" / "__init__.py")
    monkeypatch.setitem(settings.MIGRATION_MODULES, "tests", f"{runtime_dir.name}.tests.migrations")
    for name, dependencies in (
        ("first_data", []), ("second_data", [("tests", "first_data")]),
    ):
        _write_module(source_root / "runtime_migrations" / f"{name}.py", f"""\
from django.db import migrations

def applies(state):
    return True

class Migration(migrations.Migration):
    dependencies = {dependencies!r}
    operations = [migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop)]
""")
    parent = dict(name="first_data", app_label="tests", module="runtime_migrations.first_data")
    child = dict(name="second_data", app_label="resources", module="runtime_migrations.second_data")
    write_addon_manifest(addon, migrations=(child, parent) if dependent_first else (parent, child))
    importlib.invalidate_caches()
    materializer = RuntimeMigrations((addon,), labels=("resources", "tests"))

    written = materializer.materialize(apps=apps)

    assert [path.name for path in written] == ["first_data.py", "second_data.py"]
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert ("tests", "first_data") in loader.disk_migrations["resources", "second_data"].dependencies
    order = loader.graph.forwards_plan(("resources", "second_data"))
    assert order.index(("tests", "first_data")) < order.index(("resources", "second_data"))


def test_run_before_latest_is_frozen_without_rebinding(runtime_migration_probe, monkeypatch, settings) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    directory = runtime_dir / "tests" / "migrations"
    _write_module(runtime_dir / "tests" / "__init__.py")
    _write_module(directory / "__init__.py")
    _write_module(directory / "0001_target.py", """\
from django.db import migrations
class Migration(migrations.Migration):
    dependencies = []
    operations = []
""")
    monkeypatch.setitem(settings.MIGRATION_MODULES, "tests", f"{runtime_dir.name}.tests.migrations")
    source_path.write_text(
        source_path.read_text().replace(
            "    dependencies = []",
            '    dependencies = []\n    run_before = [("tests", "__latest__")]',
            1,
        ), encoding="utf-8",
    )
    importlib.invalidate_caches()

    (output,) = materializer.materialize(apps=apps)

    frozen = output.read_bytes()
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert loader.disk_migrations["resources", "rename_legacy"].run_before == [("tests", "0001_target")]
    assert ("resources", "rename_legacy") in loader.graph.forwards_plan(("tests", "0001_target"))
    _write_module(directory / "0002_later.py", """\
from django.db import migrations
class Migration(migrations.Migration):
    dependencies = [("tests", "0001_target")]
    operations = []
""")
    importlib.invalidate_caches()
    assert materializer.materialize(apps=apps) == ()
    assert output.read_bytes() == frozen
    assert MigrationLoader(None, ignore_no_migrations=True).disk_migrations[
        "resources", "rename_legacy"
    ].run_before == [("tests", "0001_target")]


def test_materialization_uses_custom_native_migration_package(runtime_migration_probe, monkeypatch, settings) -> None:
    materializer, _, _, runtime_dir, _ = runtime_migration_probe
    original = runtime_dir / "resources" / "migrations" / "0001_legacy.py"
    original_bytes = original.read_bytes()
    package = f"custom_{runtime_dir.name}"
    directory = runtime_dir.parent / package / "migrations"
    _write_module(directory.parent / "__init__.py")
    _write_module(directory / "__init__.py")
    _write_module(directory / original.name, original_bytes.decode("utf-8"))
    monkeypatch.setitem(settings.MIGRATION_MODULES, "resources", f"{package}.migrations")
    importlib.invalidate_caches()

    written = materializer.materialize(apps=apps)

    assert written == (directory / "rename_legacy.py",)
    assert original.read_bytes() == original_bytes
    assert not (original.parent / "rename_legacy.py").exists()
    assert MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes("resources") == [
        ("resources", "rename_legacy"),
    ]


@pytest.mark.parametrize("applicable", [True, False])
def test_first_authored_history_creates_only_a_validated_native_package(
    runtime_migration_probe, monkeypatch, settings, applicable,
) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    package = f"{runtime_dir.name}.resources.first_history"
    monkeypatch.setitem(settings.MIGRATION_MODULES, "resources", package)
    _write_module(source_path, f"""\
from django.db import migrations, models


def applies(project_state):
    return {applicable!r}


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.CreateModel(
        name="First", fields=[("id", models.AutoField(primary_key=True))],
    )]
""")
    directory = runtime_dir / "resources/first_history"

    if not applicable:
        with pytest.raises(RuntimeError, match="false schema guard"):
            materializer.materialize(apps=apps)
        assert not directory.exists()
        return

    with pytest.raises(ImproperlyConfigured, match="Create the importable migration package"):
        materializer.check()
    assert not directory.exists()

    written = materializer.materialize(apps=apps)

    assert written == (directory / "rename_legacy.py",)
    assert (directory / "__init__.py").read_bytes() == b""
    assert ("resources", "first") in MigrationLoader(None, ignore_no_migrations=True).project_state().models
    materializer.check()


def test_authored_history_refuses_missing_custom_parent_without_writes(
    runtime_migration_probe, monkeypatch, settings,
) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    package = f"{runtime_dir.name}.absent.history"
    monkeypatch.setitem(settings.MIGRATION_MODULES, "resources", package)
    _write_module(source_path, """\
from django.db import migrations


def applies(project_state):
    return True


class Migration(migrations.Migration):
    dependencies = []
    operations = []
""")

    with pytest.raises(ImproperlyConfigured, match="Initialize the importable parent package"):
        materializer.materialize(apps=apps)

    assert not (runtime_dir / "absent").exists()


def test_existing_numbered_materialized_history_remains_byte_identical(runtime_migration_probe) -> None:
    """A new naming convention never renames or rewrites an existing native node."""

    materializer, _, _, runtime_dir, _ = runtime_migration_probe
    (plan,) = materializer.plan()
    directory = runtime_dir / "resources" / "migrations"
    numbered = directory / "0002_rename_legacy.py"
    _write_module(numbered, materializer._render(plan))
    frozen = {path.name: path.read_bytes() for path in directory.glob("*.py")}
    importlib.invalidate_caches()

    assert materializer.materialize(apps=apps) == ()
    materializer.check()

    assert {path.name: path.read_bytes() for path in directory.glob("*.py")} == frozen
    assert not (directory / "rename_legacy.py").exists()
    assert MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes("resources") == [
        ("resources", "0002_rename_legacy"),
    ]


def test_autodetected_drop_of_an_adopted_physical_table_is_refused(runtime_migration_probe) -> None:
    """Missing declaration graph and physical table ownership are separate guards."""

    materializer, addon, _, runtime_dir, _ = runtime_migration_probe
    write_addon_manifest(addon, migrations=())
    loader = MigrationLoader(None, ignore_no_migrations=True)
    current = loader.project_state()
    adopted = current.models["resources", "legacy"].clone()
    adopted.app_label = "integrate_vcs"
    adopted.options["db_table"] = "resources_legacy"
    current.remove_model("resources", "legacy")
    current.real_apps.discard("integrate_vcs")
    current.add_model(adopted)
    directory = runtime_dir / "resources" / "migrations"
    before = {path.name: path.read_bytes() for path in directory.glob("*.py")}

    with pytest.raises(
        RuntimeError,
        match="DeleteModel would drop table 'resources_legacy', still owned by integrate_vcs.legacy",
    ):
        materializer.plan(apps=current.apps)

    assert {path.name: path.read_bytes() for path in directory.glob("*.py")} == before


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_runtime_database")
@pytest.mark.parametrize("partially_applied", [False, True])
def test_complete_native_squash_preserves_fresh_and_partial_backfill_paths(
    runtime_migration_probe, partially_applied: bool,
) -> None:
    """Django chooses the baseline for fresh installs and the original pending backfill otherwise."""

    from django.db.migrations.executor import MigrationExecutor

    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text().replace(
            '    apps.get_model("resources", "Legacy")',
            '    apps.get_model("resources", "Legacy").objects.using(schema_editor.connection.alias)'
            '.update(new_name="backfilled")',
            1,
        ), encoding="utf-8",
    )
    importlib.invalidate_caches()
    (original_transition,) = materializer.materialize(apps=apps)
    directory = runtime_dir / "resources" / "migrations"
    originals = {path.name: path.read_bytes() for path in directory.glob("*.py")}
    if partially_applied:
        executor = MigrationExecutor(connection)
        executor.migrate([("resources", "0001_legacy")])
        legacy = executor.loader.project_state([("resources", "0001_legacy")]).apps.get_model("resources", "Legacy")
        legacy._base_manager.create(old_name="kept")
    _write_module(directory / "0002_squashed.py", """\
from django.db import migrations, models

def forwards(apps, schema_editor):
    apps.get_model("resources", "Legacy").objects.using(schema_editor.connection.alias).update(new_name="backfilled")

class Migration(migrations.Migration):
    replaces = [("resources", "0001_legacy"), ("resources", "rename_legacy")]
    dependencies = []
    operations = [
        migrations.CreateModel(name="Legacy", fields=[
            ("id", models.AutoField(primary_key=True)),
            ("new_name", models.CharField(max_length=100)),
        ]),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
""")
    importlib.invalidate_caches()

    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes("resources")
    assert [migration.name for migration, _backward in executor.migration_plan(targets)] == [
        "rename_legacy" if partially_applied else "0002_squashed",
    ]
    executor.migrate(targets)
    final = executor.loader.project_state(targets)
    legacy = final.apps.get_model("resources", "Legacy")
    if partially_applied:
        assert list(legacy._base_manager.values_list("new_name", flat=True)) == ["backfilled"]
    else:
        assert legacy._base_manager.count() == 0
    assert materializer.materialize(apps=final.apps) == ()
    assert original_transition.exists()
    assert {name: (directory / name).read_bytes() for name in originals} == originals


def test_replacement_cannot_hide_a_missing_original_transition(runtime_migration_probe) -> None:
    """A replaces label is not evidence that the original data chain is available."""

    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    source_path.write_text(
        source_path.read_text().replace(
            'return model is not None and "old_name" in model.fields', "return False",
        ), encoding="utf-8",
    )
    directory = runtime_dir / "resources" / "migrations"
    _write_module(directory / "0002_squashed.py", """\
from django.db import migrations, models
class Migration(migrations.Migration):
    replaces = [("resources", "0001_legacy"), ("resources", "rename_legacy")]
    dependencies = []
    operations = [migrations.CreateModel(name="Legacy", fields=[
        ("id", models.AutoField(primary_key=True)),
        ("new_name", models.CharField(max_length=100)),
    ])]
""")
    importlib.invalidate_caches()
    before = {path.name: path.read_bytes() for path in directory.glob("*.py")}

    with pytest.raises(RuntimeError, match="original") as refusal:
        materializer.materialize(apps=apps)

    assert "rename_legacy" in str(refusal.value)
    assert {path.name: path.read_bytes() for path in directory.glob("*.py")} == before


@pytest.fixture
def native_authoring_command(runtime_migration_probe, monkeypatch, settings):
    command_module = importlib.import_module("angee.compose.management.commands.makemigrations")
    materializer, _, _, runtime_dir, _ = runtime_migration_probe
    artifact = ModuleType("native_authoring_fixture")
    artifact.RUNTIME_APPS = tuple(sorted(materializer.labels))
    monkeypatch.setitem(sys.modules, artifact.__name__, artifact)
    settings.COMPOSE_RUNTIME_MODULE = artifact.__name__

    def migrations_for(_addons, *, labels):
        assert frozenset(labels) == materializer.labels
        return materializer

    monkeypatch.setattr(command_module, "RuntimeMigrations", migrations_for)
    return ComposedMakeMigrationsCommand(), materializer, runtime_dir


def _authoring_options(command, *arguments: str) -> tuple[list[str], dict[str, Any]]:
    options = vars(command.create_parser("manage.py", "makemigrations").parse_args(arguments))
    return options.pop("args"), options


def test_native_authoring_binds_before_django_loads_autodetection_graph(native_authoring_command, monkeypatch):
    command, _, runtime_dir = native_authoring_command
    called = []

    def native_handle(self, *labels, **options):
        state = MigrationLoader(None, ignore_no_migrations=True).project_state()
        assert "new_name" in state.models["resources", "legacy"].fields
        assert "old_name" not in state.models["resources", "legacy"].fields
        called.append(labels)

    monkeypatch.setattr(NativeMakeMigrationsCommand, "handle", native_handle)
    labels, options = _authoring_options(command, "resources", "--noinput")
    command.handle(*labels, **options)

    assert called == [("resources",)]
    assert (runtime_dir / "resources/migrations/rename_legacy.py").exists()


@pytest.mark.parametrize("flag", ["--check", "--dry-run"])
def test_native_readonly_authoring_refuses_pending_sources_without_writes(
    native_authoring_command, monkeypatch, flag,
):
    command, _, runtime_dir = native_authoring_command
    before = {path: path.read_bytes() for path in runtime_dir.rglob("*.py")}

    def native_handle(*args, **kwargs):
        pytest.fail("pending authored transitions reached native autodetection")

    monkeypatch.setattr(NativeMakeMigrationsCommand, "handle", native_handle)
    labels, options = _authoring_options(command, "resources", flag)
    with pytest.raises(CommandError, match="pending addon runtime migration"):
        command.handle(*labels, **options)

    assert {path: path.read_bytes() for path in runtime_dir.rglob("*.py")} == before


@pytest.mark.parametrize("reverse_addons", [False, True])
def test_native_merge_stages_two_addons_without_ordering_or_overwriting_history(
    runtime_migration_probe, native_authoring_command, reverse_addons, monkeypatch,
):
    command, materializer, runtime_dir = native_authoring_command
    _, addon, source_path, _, source_root = runtime_migration_probe
    peer_root = source_root.parent / "peer"
    _write_module(peer_root / "__init__.py")
    peer_source = peer_root / "add_marker.py"
    _write_module(peer_source, '''from django.db import migrations, models

def applies(state):
    model = state.models.get(("resources", "legacy"))
    return model is not None and "old_name" in model.fields and "marker" not in model.fields

class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.AddField(model_name="legacy", name="marker", field=models.BooleanField(default=False))]
''')
    peer = make_addon(name="example.peer", path=peer_root, migrations=(
        dict(name="add_marker", app_label="resources", module="add_marker"),
    ))
    materializer.addons = (peer, addon) if reverse_addons else (addon, peer)
    directory = runtime_dir / "resources/migrations"
    _write_module(directory / "0002_reserved.py", '''from django.db import migrations
class Migration(migrations.Migration):
    dependencies = [("resources", "0001_legacy")]
    operations = []
''')
    before = {path: path.read_bytes() for path in directory.glob("*.py")}
    sources = {path: path.read_bytes() for path in (source_path, peer_source)}

    with pytest.raises(RuntimeError, match="multiple migration leaves"):
        materializer.plan()
    labels, options = _authoring_options(command, "resources", "--merge", "--name", "reserved", "--noinput")
    with pytest.raises(CommandError, match="already used"):
        command.handle(*labels, **options)
    assert {path: path.read_bytes() for path in directory.glob("*.py")} == before

    for flag in ("--dry-run", "--check"):
        labels, options = _authoring_options(command, "resources", "--merge", "--name", "join_rehomes", flag)
        with pytest.raises(CommandError, match="multiple migration leaves"):
            command.handle(*labels, **options)
        assert {path: path.read_bytes() for path in directory.glob("*.py")} == before

    checked = []
    check_drops = materializer._check_autodetected_drops

    def check_merged_state(loader, from_state, to_state, *, app_labels):
        assert loader.detect_conflicts() == {}
        checked.append(True)
        check_drops(loader, from_state, to_state, app_labels=app_labels)

    monkeypatch.setattr(materializer, "_check_autodetected_drops", check_merged_state)
    labels, options = _authoring_options(command, "resources", "--merge", "--name", "join_rehomes", "--noinput")
    with _default_database(":memory:", DummyDatabaseWrapper):
        command.handle(*labels, **options)

    assert checked == [True]
    loader = MigrationLoader(None, ignore_no_migrations=True)
    baseline = [("resources", "0002_reserved")]
    for name, source in (("rename_legacy", source_path), ("add_marker", peer_source)):
        migration = loader.disk_migrations["resources", name]
        assert migration.dependencies == baseline
        assert (directory / f"{name}.py").read_bytes().startswith(sources[source])
        assert migration.angee_source_sha256 == hashlib.sha256(sources[source]).hexdigest()
    assert loader.graph.leaf_nodes("resources") == [("resources", "0002_join_rehomes")]
    merge = loader.disk_migrations["resources", "0002_join_rehomes"]
    assert set(merge.dependencies) == {("resources", "rename_legacy"), ("resources", "add_marker")}
    assert merge.operations == []
    fields = loader.project_state().models["resources", "legacy"].fields
    assert {"new_name", "marker"} <= fields.keys()
    assert "old_name" not in fields
    assert {path: path.read_bytes() for path in sources} == sources
    assert {path: path.read_bytes() for path in before} == before
    assert materializer.plan(apps=apps) == ()
    assert materializer.materialize(apps=apps) == ()


def test_native_merge_bypasses_pending_binding_for_existing_host_branches(
    native_authoring_command, monkeypatch,
):
    command, materializer, runtime_dir = native_authoring_command
    directory = runtime_dir / "resources/migrations"
    for name in ("0002_left", "0002_right"):
        _write_module(directory / f"{name}.py", '''from django.db import migrations
class Migration(migrations.Migration):
    dependencies = [("resources", "0001_legacy")]
    operations = []
''')
    called = []

    def refuse_binding(**kwargs):
        pytest.fail("merge attempted to bind onto a graph it must repair first")

    monkeypatch.setattr(materializer, "materialize", refuse_binding)
    monkeypatch.setattr(NativeMakeMigrationsCommand, "handle", lambda self, *args, **kwargs: called.append(args))
    labels, options = _authoring_options(command, "resources", "--merge", "--noinput")
    command.handle(*labels, **options)

    assert called == [("resources",)]
    assert not (directory / "rename_legacy.py").exists()


def test_native_writer_preflights_every_destination_before_writing(runtime_migration_probe):
    _, _, _, runtime_dir, _ = runtime_migration_probe
    directory = runtime_dir / "resources/migrations"
    before = {path: path.read_bytes() for path in directory.glob("*.py")}
    command = ComposedMakeMigrationsCommand()
    command.include_header = False
    command.dry_run = False
    safe = migrations.Migration("0002_safe", "resources")
    collision = migrations.Migration("0001_legacy", "resources")

    with pytest.raises(CommandError, match="Refusing to overwrite existing migration"):
        command.write_migration_files({"resources": [safe, collision]})

    assert {path: path.read_bytes() for path in directory.glob("*.py")} == before
    assert not (directory / "0002_safe.py").exists()


def test_native_readonly_writer_does_not_create_a_missing_custom_package(runtime_migration_probe, settings):
    _, _, _, runtime_dir, _ = runtime_migration_probe
    package = f"{runtime_dir.name}.resources.uncreated_history"
    settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, "resources": package}
    command = ComposedMakeMigrationsCommand()
    command.include_header = False
    command.dry_run = True

    with pytest.raises(CommandError, match="read-only run cannot create"):
        command.write_migration_files({"resources": [migrations.Migration("0001_initial", "resources")]})

    assert not (runtime_dir / "resources/uncreated_history").exists()


def test_native_update_refuses_an_immutable_materialized_source(runtime_migration_probe):
    materializer, _, _, runtime_dir, _ = runtime_migration_probe
    (path,) = materializer.materialize(apps=apps)
    before = path.read_bytes()
    bound = MigrationLoader(None, ignore_no_migrations=True).disk_migrations["resources", "rename_legacy"]
    command = ComposedMakeMigrationsCommand()
    command.include_header = False
    command.dry_run = False

    with pytest.raises(CommandError, match="Cannot update immutable addon migration"):
        command.write_migration_files({"resources": [bound]}, {"resources": str(path)})

    assert path.read_bytes() == before
    assert (runtime_dir / "resources/migrations/0001_legacy.py").exists()


def test_native_writer_permits_an_ordinary_native_update(runtime_migration_probe):
    _, _, _, runtime_dir, _ = runtime_migration_probe
    directory = runtime_dir / "resources/migrations"
    previous = directory / "0001_legacy.py"
    original = MigrationLoader(None, ignore_no_migrations=True).disk_migrations["resources", "0001_legacy"]
    updated = migrations.Migration("0002_rewritten", "resources")
    updated.operations = original.operations
    command = ComposedMakeMigrationsCommand()
    command.include_header = False
    command.dry_run = False
    command.verbosity = 0
    command.scriptable = False
    command.written_files = []

    command.write_migration_files({"resources": [updated]}, {"resources": str(previous)})

    assert (directory / "0002_rewritten.py").exists()
    assert not previous.exists()


def test_scoped_plan_keeps_native_prerequisite_closure(runtime_migration_probe, monkeypatch, settings):
    _, addon, source_path, runtime_dir, source_root = runtime_migration_probe
    _write_module(runtime_dir / "integrate_vcs/__init__.py")
    _write_module(runtime_dir / "integrate_vcs/migrations/__init__.py")
    monkeypatch.setitem(settings.MIGRATION_MODULES, "integrate_vcs", f"{runtime_dir.name}.integrate_vcs.migrations")
    _write_module(source_root / "prepare.py", '''from django.db import migrations, models

def applies(state):
    return ("integrate_vcs", "ready") not in state.models

class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.CreateModel(name="Ready", fields=[("id", models.AutoField(primary_key=True))])]
''')
    source_path.write_text(
        source_path.read_text().replace(
            '    dependencies = []', '    dependencies = [("integrate_vcs", "prepare")]', 1,
        ),
        encoding="utf-8",
    )
    write_addon_manifest(addon, migrations=(
        dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),
        dict(name="prepare", app_label="integrate_vcs", module="prepare"),
    ))
    materializer = RuntimeMigrations((addon,), labels=("resources", "integrate_vcs"))

    assert [plan.name for plan in materializer.plan(app_labels=("resources",))] == ["prepare", "rename_legacy"]
    assert not (runtime_dir / "integrate_vcs/migrations/prepare.py").exists()


def test_scoped_plan_excludes_unrelated_pending_state_and_leaf_conflicts(
    runtime_migration_probe, monkeypatch, settings,
):
    _, addon, _, runtime_dir, source_root = runtime_migration_probe
    other = runtime_dir / "integrate_vcs/migrations"
    _write_module(other.parent / "__init__.py")
    _write_module(other / "__init__.py")
    monkeypatch.setitem(settings.MIGRATION_MODULES, "integrate_vcs", f"{runtime_dir.name}.integrate_vcs.migrations")
    for name in ("0001_left", "0001_right"):
        _write_module(other / f"{name}.py", '''from django.db import migrations
class Migration(migrations.Migration):
    dependencies = []
    operations = []
''')
    _write_module(source_root / "unrelated.py", '''from django.db import migrations, models

def applies(state):
    raise AssertionError("unselected guard must not run")

class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.CreateModel(name="Unrelated", fields=[("id", models.AutoField(primary_key=True))])]
''')
    write_addon_manifest(addon, migrations=(
        dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),
        dict(name="unrelated", app_label="integrate_vcs", module="unrelated"),
    ))
    materializer = RuntimeMigrations((addon,), labels=("resources", "integrate_vcs"))
    inspected = []

    def inspect_drop_state(loader, from_state, to_state, *, app_labels):
        assert ("integrate_vcs", "unrelated") not in from_state.models
        assert "new_name" in from_state.models["resources", "legacy"].fields
        inspected.append(app_labels)

    monkeypatch.setattr(materializer, "_check_autodetected_drops", inspect_drop_state)
    plans = materializer.plan(apps=apps, app_labels=("resources",))

    assert [plan.name for plan in plans] == ["rename_legacy"]
    assert inspected == [{"resources"}]
    assert not (other / "unrelated.py").exists()
    with pytest.raises(RuntimeError, match="multiple migration leaves"):
        materializer.plan()


@pytest.mark.parametrize("color_flag", ["--no-color", "--force-color"])
def test_host_migration_authoring_preserves_native_command_color_options(monkeypatch, color_flag: str) -> None:
    output = StringIO()
    command = HostMakeMigrationsCommand(stdout=output)
    labels, options = _authoring_options(command, "resources", color_flag, "--noinput")
    captured = []

    def delegate(self, *app_labels, **parsed_options):
        captured.append((app_labels, parsed_options["no_color"], parsed_options["force_color"]))
        self.stdout.write(self.style.SUCCESS("Delegated"))

    monkeypatch.setattr(ComposedMakeMigrationsCommand, "handle", delegate)
    command.handle(*labels, **options)
    assert captured == [(("resources",), color_flag == "--no-color", color_flag == "--force-color")]
    assert ("\x1b[" in output.getvalue()) is (color_flag == "--force-color")


def test_fresh_host_authors_native_initials_before_guarded_transitions(tmp_path: Path) -> None:
    """One public command writes schema prerequisites and retains the guarded data transition."""
    root = Path(__file__).resolve().parents[1]
    package = tmp_path / "bootstrap_app"
    _write_module(package / "__init__.py")
    _write_module(package / "addon.toml", """[addon]
name = "bootstrap_app"
[[migrations]]
name = "record_bootstrap"
app_label = "bootstrap_app"
module = "runtime_migrations.record_bootstrap"
""")
    _write_module(package / "models.py", """from django.db import models
class Entry(models.Model):
    runtime = True
    value = models.CharField(max_length=40)
    class Meta:
        abstract = True
""")
    _write_module(package / "runtime_migrations" / "__init__.py")
    _write_module(package / "runtime_migrations" / "record_bootstrap.py", """from django.db import migrations
def applies(state):
    return ("bootstrap_app", "entry") in state.models
def forwards(apps, schema_editor):
    apps.get_model("bootstrap_app", "Entry")._base_manager.using(schema_editor.connection.alias).create(
        value="transition ran",
    )
class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
""")
    _write_module(tmp_path / "bootstrap_settings.py", f"""import os
SECRET_KEY = "fresh-authoring"
INSTALLED_APPS = ["bootstrap_app", "angee.build.ProjectBuildConfig" if os.environ.get("COMPOSE_BUILD")
    else "angee.apps.AngeeConfig"]
COMPOSE_APP_DECLARATIONS = "angee.build.ManifestDeclarations"
COMPOSE_RUNTIME_MODULE = "runtime"
COMPOSE_RUNTIME_DIR = {str(tmp_path / "runtime")!r}
DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": {str(tmp_path / "fresh.sqlite3")!r}}}}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
""")
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("ANGEE_", "YAMLCONF_"))
        and name not in {"DATABASE_URL", "DJANGO_SETTINGS_MODULE", "PYTHONPATH", "COMPOSE_BUILD"}
    }
    environment.update(
        DJANGO_SETTINGS_MODULE="bootstrap_settings", PYTHONPATH=os.pathsep.join((str(tmp_path), str(root))),
    )
    built = subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=tmp_path, env={**environment, "COMPOSE_BUILD": "1"},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run(
        [sys.executable, "-c", r"""
from pathlib import Path
import django
django.setup()
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

directory = Path("runtime/bootstrap_app/migrations")
assert list(directory.glob("*.py")) == [directory / "__init__.py"]
for flag in ("--check", "--dry-run", "--merge", "--empty", "--update"):
    try:
        call_command("makemigrations", "bootstrap_app", flag, interactive=False, verbosity=0)
    except CommandError as error:
        assert "not applicable" in str(error), str(error)
    else:
        raise AssertionError(f"{flag} silently accepted a transition without its initial prerequisite")
    assert list(directory.glob("*.py")) == [directory / "__init__.py"]
call_command("makemigrations", interactive=False, verbosity=0)
loader = MigrationLoader(None, ignore_no_migrations=True)
transition = loader.disk_migrations["bootstrap_app", "record_bootstrap"]
assert transition.dependencies == [("bootstrap_app", "0001_initial")]
assert transition.angee_origin == "bootstrap_app:record_bootstrap"
assert MigrationRecorder(connection).applied_migrations() == {}
frozen = {path: path.read_bytes() for path in directory.glob("*.py")}
call_command("makemigrations", interactive=False, verbosity=0)
call_command("makemigrations", check_changes=True, dry_run=True, interactive=False, verbosity=0)
assert {path: path.read_bytes() for path in directory.glob("*.py")} == frozen
call_command("migrate", interactive=False, verbosity=0)
rows = apps.get_model("bootstrap_app", "Entry")._base_manager
assert list(rows.values_list("value", flat=True)) == ["transition ran"]
"""],
        cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
