"""Addon-owned migrations materialized into Django runtime graphs."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ModelState, ProjectState

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
    addon = SimpleNamespace(
        name="example.demo",
        _addon_contract=AddonContract(
            name="example.demo",
            migrations=(
                AddonMigration(
                    "rename_legacy",
                    "resources",
                    "runtime_migrations.rename_legacy",
                ),
            ),
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
    addon._addon_contract = AddonContract(
        name="example.demo",
        migrations=(
            AddonMigration("rename_legacy", "resources", "runtime_migrations.rename_legacy"),
            AddonMigration("add_marker", "resources", "runtime_migrations.add_marker"),
        ),
    )
    importlib.invalidate_caches()

    written = materializer.materialize()

    assert [path.name for path in written] == ["0002_rename_legacy.py", "0003_add_marker.py"]
    second = (runtime_dir / "resources" / "migrations" / "0003_add_marker.py").read_text(encoding="utf-8")
    assert 'Migration.dependencies.append(("resources", "0002_rename_legacy"))' in second


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
    monkeypatch.setitem(settings.MIGRATION_MODULES, "iam", f"{materializer.runtime_module}.iam.migrations")
    importlib.invalidate_caches()

    materializer.materialize()

    text = (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").read_text(encoding="utf-8")
    assert (
        '("iam", "0004_current") if dependency == ("iam", "__latest__")'
        in text
    )


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
            AddonMigration("Bad-Name", "resources", "runtime_migrations.rename_legacy"),
            "migration name must be a lower-case Python identifier",
        ),
        (
            AddonMigration("rename_legacy", "unknown", "runtime_migrations.rename_legacy"),
            "unknown runtime migration target 'unknown'",
        ),
    ],
)
def test_rejects_invalid_declarations(runtime_migration_probe, declaration, message: str) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    addon._addon_contract = AddonContract(name="example.demo", migrations=(declaration,))

    with pytest.raises(RuntimeError, match=message):
        materializer.materialize()


def test_rejects_duplicate_declared_origins(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    declaration = AddonMigration("rename_legacy", "resources", "runtime_migrations.rename_legacy")
    addon._addon_contract = AddonContract(
        name="example.demo",
        migrations=(declaration, declaration),
    )

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
    addon._addon_contract = AddonContract(
        name="example.demo",
        migrations=(
            AddonMigration("rename_legacy", "resources", "runtime_migrations.rename_legacy"),
            AddonMigration("broken", "resources", "runtime_migrations.broken"),
        ),
    )
    importlib.invalidate_caches()

    with pytest.raises(RuntimeError, match="example.demo:broken: source module must define applies"):
        materializer.materialize()

    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def _old_relationship_state() -> ProjectState:
    from angee.parties.models import Relationship

    relationship = ModelState.from_model(Relationship)
    relationship.fields.pop("party")
    relationship.fields.pop("other_party")
    relationship.fields.pop("other_name")
    relationship.fields["from_party"] = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="relationships",
    )
    relationship.fields["to_party"] = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="inbound_relationships",
    )
    relationship.options["ordering"] = ("from_party", "sqid")
    relationship.options["constraints"] = [
        models.UniqueConstraint(
            fields=("from_party", "to_party", "kind"),
            name="uq_relationship_edge",
        ),
        models.CheckConstraint(
            condition=~models.Q(from_party=models.F("to_party")),
            name="ck_relationship_distinct_parties",
        ),
    ]
    state = ProjectState()
    state.add_model(relationship)
    return state


def test_parties_relationship_migration_preserves_renamed_foreign_keys() -> None:
    """The append-only source migration owns only the lossless anchor transition."""

    module = importlib.import_module("angee.parties.runtime_migrations.relationship_anchor")
    old_state = _old_relationship_state()

    assert module.applies(old_state) is True
    migrated = module.Migration("probe", "parties").mutate_state(old_state)
    relationship = migrated.models["parties", "relationship"]

    assert "party" in relationship.fields
    assert "other_party" in relationship.fields
    assert "other_name" in relationship.fields
    assert "from_party" not in relationship.fields
    assert "to_party" not in relationship.fields
    assert relationship.fields["other_party"].null is True
    assert relationship.fields["other_party"].remote_field.on_delete is models.SET_NULL
    assert relationship.options["ordering"] == ("party", "sqid")
    assert {constraint.name for constraint in relationship.options["constraints"]} == {
        "uq_relationship_edge",
        "ck_relationship_distinct_parties",
        "ck_relationship_has_other",
    }

def test_parties_relationship_migration_applies_only_to_exact_old_state() -> None:
    from angee.parties.models import Relationship

    module = importlib.import_module("angee.parties.runtime_migrations.relationship_anchor")
    current = ProjectState()
    current.add_model(ModelState.from_model(Relationship))
    mixed = _old_relationship_state()
    mixed.models["parties", "relationship"].fields["party"] = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="mixed_relationships",
    )

    assert module.applies(ProjectState()) is False
    assert module.applies(current) is False
    with pytest.raises(ImproperlyConfigured, match="partial Relationship field transition"):
        module.applies(mixed)


def test_parties_handle_confirmation_migration_adds_materialized_winner_state() -> None:
    from angee.parties.models import Handle

    module = importlib.import_module("angee.parties.runtime_migrations.handle_party_link_confirmed")
    old_state = ProjectState()
    handle = ModelState.from_model(Handle)
    handle.fields.pop("party_link_confirmed")
    old_state.add_model(handle)

    assert module.applies(old_state) is True
    migrated = module.Migration("probe", "parties").mutate_state(old_state)
    field = migrated.models["parties", "handle"].fields["party_link_confirmed"]

    assert isinstance(field, models.BooleanField)
    assert field.default is False
    assert module.applies(migrated) is False


def test_parties_handle_normalized_value_migration_adds_required_indexed_field() -> None:
    from angee.parties.models import Handle

    module = importlib.import_module("angee.parties.runtime_migrations.handle_normalized_value")
    old_state = ProjectState()
    handle = ModelState.from_model(Handle)
    handle.fields.pop("normalized_value")
    old_state.add_model(handle)

    assert module.applies(old_state) is True
    migrated = module.Migration("probe", "parties").mutate_state(old_state)
    field = migrated.models["parties", "handle"].fields["normalized_value"]

    assert isinstance(field, models.CharField)
    assert field.null is False
    assert field.db_index is True
    assert field.editable is False
    assert module.applies(migrated) is False


@pytest.mark.django_db(transaction=True)
def test_parties_handle_normalized_value_migration_backfills_existing_rows() -> None:
    module = importlib.import_module("angee.parties.runtime_migrations.handle_normalized_value")

    class LegacyHandle(models.Model):
        platform = models.CharField(max_length=8)
        value = models.CharField(max_length=512)
        normalized_value = models.CharField(max_length=512, null=True)

        class Meta:
            app_label = "tests"
            db_table = "test_legacy_handle_normalized_value"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(LegacyHandle)
    historical_apps = SimpleNamespace(get_model=lambda *args: LegacyHandle)
    try:
        LegacyHandle._base_manager.bulk_create(
            [
                LegacyHandle(platform="email", value=" Alice.Smith+work@GMAIL.com "),
                LegacyHandle(platform="email", value="User@Example.COM"),
                LegacyHandle(platform="phone", value=" +420 123 456 "),
            ]
        )
        with connection.schema_editor() as schema_editor:
            module.backfill_normalized_values(historical_apps, schema_editor)

        assert list(
            LegacyHandle._base_manager.order_by("id").values_list("normalized_value", flat=True)
        ) == ["alicesmith@gmail.com", "user@example.com", "+420 123 456"]
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(LegacyHandle)


def _old_nexus_state() -> ProjectState:
    from angee.nexus.models import Tie

    tie = ModelState.from_model(Tie)
    tie.fields.pop("party_a")
    tie.fields.pop("party_b")
    tie.fields.pop("a_to_b_count")
    tie.fields.pop("b_to_a_count")
    tie.fields["party"] = models.OneToOneField(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="tie",
    )
    tie.fields["outbound_count"] = models.PositiveIntegerField(default=0)
    tie.fields["inbound_count"] = models.PositiveIntegerField(default=0)
    tie.fields["cadence_days"] = models.PositiveIntegerField(null=True, blank=True)
    tie.fields["touch_due_at"] = models.DateTimeField(null=True, blank=True, db_index=True)
    tie.options["constraints"] = []
    state = ProjectState()
    state.add_model(tie)
    return state


def test_nexus_tie_pair_migration_replaces_only_the_exact_legacy_shape() -> None:
    from angee.nexus.models import Cadence, Tie

    module = importlib.import_module("angee.nexus.runtime_migrations.tie_pair_reshape")
    old_state = _old_nexus_state()

    assert module.applies(old_state) is True
    migrated = module.Migration("probe", "nexus").mutate_state(old_state)
    assert ("nexus", "tie") not in migrated.models

    current = ProjectState()
    current.add_model(ModelState.from_model(Tie))
    current.add_model(ModelState.from_model(Cadence))
    assert module.applies(ProjectState()) is False
    assert module.applies(current) is False

    mixed = _old_nexus_state()
    mixed.models["nexus", "tie"].fields["party_a"] = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="mixed_ties",
    )
    with pytest.raises(ImproperlyConfigured, match="partial Tie field transition"):
        module.applies(mixed)


@pytest.mark.django_db(transaction=True)
def test_nexus_tie_pair_migration_refuses_to_discard_cadence_values() -> None:
    module = importlib.import_module("angee.nexus.runtime_migrations.tie_pair_reshape")

    class LegacyNexusTie(models.Model):
        cadence_days = models.PositiveIntegerField(null=True, blank=True)

        class Meta:
            app_label = "tests"
            db_table = "test_legacy_nexus_tie_guard"

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(LegacyNexusTie)
    historical_apps = SimpleNamespace(get_model=lambda *args: LegacyNexusTie)
    try:
        LegacyNexusTie._base_manager.create(cadence_days=None)
        with connection.schema_editor() as schema_editor:
            module.assert_no_legacy_cadence_values(historical_apps, schema_editor)

        LegacyNexusTie._base_manager.create(cadence_days=30)
        with connection.schema_editor() as schema_editor:
            with pytest.raises(RuntimeError, match="legacy cadence values"):
                module.assert_no_legacy_cadence_values(historical_apps, schema_editor)
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(LegacyNexusTie)


def test_parties_source_migration_is_not_discovered_as_an_app_migration() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)

    assert ("parties", "relationship_anchor") not in loader.disk_migrations


def test_rejects_source_in_djangos_conventional_migrations_package(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    addon._addon_contract = AddonContract(
        name="example.demo",
        migrations=(
            AddonMigration("rename_legacy", "resources", "migrations.rename_legacy"),
        ),
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
    addon._addon_contract = AddonContract(
        name="example.demo",
        migrations=(
            AddonMigration("rename_legacy", "resources", "runtime_migrations.rename_legacy"),
            AddonMigration("add_marker", "resources", "runtime_migrations.add_marker"),
        ),
    )
    importlib.invalidate_caches()

    with pytest.raises(RuntimeError, match="source migration must end with a newline"):
        materializer.materialize()

    assert not (runtime_dir / "resources" / "migrations" / "0002_rename_legacy.py").exists()


def test_materialized_origin_uses_app_config_name(runtime_migration_probe) -> None:
    materializer, addon, _, _, _ = runtime_migration_probe
    addon._addon_contract = AddonContract(
        name="stale.manifest.name",
        migrations=(
            AddonMigration("rename_legacy", "resources", "runtime_migrations.rename_legacy"),
        ),
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
