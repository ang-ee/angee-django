"""Addon-owned migrations materialized into Django runtime graphs."""

from __future__ import annotations

import hashlib
import importlib
import logging
import sys
from pathlib import Path

import pytest
from django.db import models
from django.db.migrations.loader import MigrationLoader

from angee.compose.migrations import RuntimeMigrations
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

    written = materializer.materialize()

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

    written = materializer.materialize()

    assert [path.name for path in written] == ["0002_rename_legacy.py", "0003_add_marker.py"]
    assert materializer.materialize() == ()
    state = MigrationLoader(None, ignore_no_migrations=True).project_state()
    assert "marker" in state.models["resources", "legacy"].fields


@pytest.mark.parametrize("dependent_first", [True, False])
def test_deferred_cross_app_declaration_depends_on_present_planned_leaf(
    runtime_migration_probe, monkeypatch, settings, dependent_first: bool
) -> None:
    """A later-round declaration receives a concrete native cross-app dependency."""

    materializer, addon, _, runtime_dir, source_root = runtime_migration_probe
    iam_migrations = runtime_dir / "iam" / "migrations"
    _write_module(runtime_dir / "iam" / "__init__.py")
    _write_module(iam_migrations / "__init__.py")
    _write_module(
        source_root / "runtime_migrations" / "after_flag.py",
        """from django.db import migrations
def applies(project_state):
    return ("iam", "flag") in project_state.models
class Migration(migrations.Migration):
    dependencies = []
    operations = []
""",
    )
    _write_module(
        source_root / "runtime_migrations" / "add_flag.py",
        """from django.db import migrations, models
def applies(project_state):
    return ("iam", "flag") not in project_state.models
class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.CreateModel(name="Flag", fields=[("id", models.AutoField(primary_key=True))])]
""",
    )
    dependent = dict(name="after_flag", app_label="resources", module="runtime_migrations.after_flag")
    enabling = dict(name="add_flag", app_label="iam", module="runtime_migrations.add_flag")
    write_addon_manifest(addon, migrations=(dependent, enabling) if dependent_first else (enabling, dependent))
    monkeypatch.setitem(settings.MIGRATION_MODULES, "iam", f"{runtime_dir.name}.iam.migrations")
    importlib.invalidate_caches()
    materializer = RuntimeMigrations((addon,), runtime_dir=runtime_dir, labels=("resources", "iam"))

    written = materializer.materialize()

    assert [path.name for path in written] == ["0001_add_flag.py", "0002_after_flag.py"]
    deferred = (runtime_dir / "resources" / "migrations" / "0002_after_flag.py").read_text()
    assert 'Migration.dependencies.append(("iam", "0001_add_flag"))' in deferred
    assert materializer.materialize() == ()


def test_latest_dependency_resolves_to_other_runtime_leaf(runtime_migration_probe, monkeypatch, settings) -> None:
    materializer, _, source_path, runtime_dir, _ = runtime_migration_probe
    iam_migrations = runtime_dir / "iam" / "migrations"
    _write_module(runtime_dir / "iam" / "__init__.py")
    _write_module(iam_migrations / "__init__.py")
    _write_module(
        iam_migrations / "0004_current.py",
        """\
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = []
    operations = []
""",
    )
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            "dependencies = []", 'dependencies = [("iam", "__latest__")]', 1
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(settings.MIGRATION_MODULES, "iam", f"{runtime_dir.name}.iam.migrations")
    importlib.invalidate_caches()

    materializer.materialize()

    text = (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").read_text(encoding="utf-8")
    assert '("iam", "0004_current") if dependency == ("iam", "__latest__")' in text


def test_materialization_is_idempotent(runtime_migration_probe) -> None:
    materializer, _, _, _, _ = runtime_migration_probe

    first = materializer.materialize()
    second = materializer.materialize()

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


def test_changed_released_source_fails_instead_of_rewriting(runtime_migration_probe) -> None:
    materializer, _, source_path, _, _ = runtime_migration_probe
    materializer.materialize()
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "# changed\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="source digest changed"):
        materializer.materialize()


def test_declared_compatible_released_source_remains_immutable(runtime_migration_probe) -> None:
    """An exact historical source digest may coexist with its current declaration."""

    materializer, addon, source_path, _, _ = runtime_migration_probe
    (output,) = materializer.materialize()
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

    assert materializer.materialize() == ()
    output.write_text(
        output.read_text(encoding="utf-8").replace("def forwards", "def edited_forwards", 1),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="materialized body digest changed"):
        materializer.materialize()


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
        materializer.materialize()


def test_changed_materialized_body_fails_instead_of_becoming_history(runtime_migration_probe) -> None:
    materializer, _, _, runtime_dir, _ = runtime_migration_probe
    (output,) = materializer.materialize()
    output.write_text(
        output.read_text(encoding="utf-8").replace("def forwards", "def edited_forwards", 1),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="materialized body digest changed"):
        materializer.materialize()

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
        materializer.materialize()


def test_rejects_duplicate_declared_origins(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    declaration = dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy")
    write_addon_manifest(addon, migrations=(declaration, declaration))

    with pytest.raises(RuntimeError, match="duplicate addon runtime migration origin example.demo:rename_legacy"):
        materializer.materialize()


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
        materializer.materialize()


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
        materializer.materialize()


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
        materializer.materialize()


def test_rejects_duplicate_materialized_origins(runtime_migration_probe) -> None:
    materializer, _, _, runtime_dir, _ = runtime_migration_probe
    (output,) = materializer.materialize()
    duplicate = runtime_dir / "resources" / "migrations" / "0003_duplicate.py"
    duplicate.write_text(output.read_text(encoding="utf-8"), encoding="utf-8")
    importlib.invalidate_caches()

    with pytest.raises(
        RuntimeError,
        match="duplicate materialized addon runtime migration origin example.demo:rename_legacy",
    ):
        materializer.materialize()


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
        materializer.materialize()


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
        materializer.materialize()

    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def test_rejects_source_in_djangos_conventional_migrations_package(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    write_addon_manifest(
        addon, migrations=(dict(name="rename_legacy", app_label="resources", module="migrations.rename_legacy"),)
    )

    with pytest.raises(RuntimeError, match="must live outside Django's conventional migrations package"):
        materializer.materialize()


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
        materializer.materialize()


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
        materializer.materialize()

    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def test_materialized_origin_uses_app_config_name(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    write_addon_manifest(
        addon,
        migrations=(dict(name="rename_legacy", app_label="resources", module="runtime_migrations.rename_legacy"),),
    )

    (output,) = materializer.materialize()

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
        materializer.materialize()


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
