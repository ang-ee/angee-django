"""Work stage identities stay distinct and retained resource xrefs upgrade safely."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState
from rebac import system_context

from angee.compose.migrations import RuntimeMigrations
from angee.base.fields.identity import SqidField
from angee.work.models import Stage
from angee.work.runtime_migrations import stage_public_identity
from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase
from tests.tables import model_tables


def _state() -> ProjectState:
    state = ProjectState()
    state.add_model(ModelState("work", "Stage", [("id", models.AutoField(primary_key=True))]))
    state.add_model(ModelState("resources", "Resource", [
        ("id", models.AutoField(primary_key=True)),
        ("target_model", models.CharField(max_length=120)),
        ("target_id", models.CharField(max_length=120)),
        ("xref", models.CharField(max_length=120)),
        ("source_addon", models.CharField(max_length=120)),
    ], options={"db_table": "test_work_identity_resource"}))
    return state


def test_work_stage_prefix_is_canonical_and_has_no_legacy_alias() -> None:
    old = SqidField(real_field_name="id", prefix="stg_", min_length=8)
    field = Stage._meta.get_field("sqid")
    assert isinstance(field, SqidField)
    assert Stage.public_id_from_pk(29).startswith("wst_")
    assert field.public_id_to_value(Stage.public_id_from_pk(29)) == 29
    assert field.public_id_to_value(old.public_id_from_value(29)) is None


def test_work_identity_migration_is_declared_and_guarded(tmp_path: Path) -> None:
    state = _state()
    assert stage_public_identity.applies(state)
    assert not stage_public_identity.applies(ProjectState())
    without_work = state.clone()
    without_work.remove_model("work", "stage")
    assert not stage_public_identity.applies(without_work)
    scheduled = RuntimeMigrations(
        (apps.get_app_config("work"),), runtime_dir=tmp_path, labels=("resources", "work"),
    ).schedule(state, only=frozenset({"angee.work:stage_public_identity"}))
    assert [entry.origin for entry in scheduled] == ["angee.work:stage_public_identity"]


@pytest.mark.django_db(transaction=True)
def test_stage_resource_migration_preserves_rows_and_xrefs_and_is_idempotent() -> None:
    state = _state()
    ledger = state.apps.get_model("resources", "Resource")
    old = SqidField(real_field_name="id", prefix="stg_", min_length=8)
    legacy_id = old.public_id_from_value(29)
    current_id = Stage.public_id_from_pk(29)
    with model_tables((ledger,)):
        changed = ledger.objects.create(
            target_model="work.Stage", target_id=legacy_id, xref="stage.ready", source_addon="angee.work",
        )
        other = ledger.objects.create(
            target_model="crm.Stage", target_id=legacy_id, xref="stage.pipeline", source_addon="consumer",
        )
        canonical = ledger.objects.create(
            target_model="work.Stage", target_id=current_id, xref="stage.current", source_addon="consumer",
        )
        migration = stage_public_identity.Migration("stage_public_identity", "resources")
        with connection.schema_editor() as editor:
            migration.apply(state, editor)
        changed.refresh_from_db()
        other.refresh_from_db()
        canonical.refresh_from_db()
        assert (changed.pk, changed.target_model, changed.target_id, changed.xref, changed.source_addon) == (
            changed.pk, "work.Stage", current_id, "stage.ready", "angee.work",
        )
        assert other.target_id == legacy_id
        assert canonical.target_id == current_id
        before = list(ledger.objects.order_by("pk").values())
        with connection.schema_editor() as editor:
            migration.apply(state, editor)
        assert list(ledger.objects.order_by("pk").values()) == before
        assert ledger.objects.get(source_addon="angee.work", xref="stage.ready").target_id == current_id


@pytest.mark.django_db(transaction=True)
def test_malformed_legacy_stage_reference_fails_without_partial_rewrites() -> None:
    state = _state()
    ledger = state.apps.get_model("resources", "Resource")
    old = SqidField(real_field_name="id", prefix="stg_", min_length=8)
    with model_tables((ledger,)):
        ledger.objects.create(target_model="work.Stage", target_id=old.public_id_from_value(29), xref="valid")
        ledger.objects.create(target_model="work.Stage", target_id="stg_invalid", xref="broken")
        before = list(ledger.objects.order_by("pk").values())
        with pytest.raises(ValueError, match="invalid legacy work.Stage public ID"):
            with connection.schema_editor() as editor:
                stage_public_identity.Migration("stage_public_identity", "resources").apply(state, editor)
        assert list(ledger.objects.order_by("pk").values()) == before


class WorkStageResourceIdentityTests(WorkCase):
    def test_migrated_xref_resolves_the_same_native_stage_and_keeps_queue_pointer(self):
        resource = apps.get_model("resources", "Resource")
        stage = self.stages["Ready"]
        legacy = SqidField(real_field_name="id", prefix="stg_", min_length=8)
        with system_context(reason="test.work.stage_identity"):
            self.queue.default_stage = stage
            self.queue.save(update_fields=["default_stage", "updated_at"])
            ledger = resource.objects.create(
                source_addon="angee.work", source_path="resources/stage-identity.yaml", tier="demo",
                xref="stage.identity", content_hash="identity", target_model="work.Stage",
                target_id=legacy.public_id_from_value(stage.pk),
            )
            stage_public_identity.forwards(apps, SimpleNamespace(connection=connection))
            resolved = resource.objects.resolve("angee.work.stage.identity", model=self.Stage)
            self.assertEqual(resolved.pk, stage.pk)
            self.assertTrue(resolved.sqid.startswith("wst_"))
            self.assertEqual(resource.objects.seeding(stage).get().pk, ledger.pk)
            self.queue.refresh_from_db()
            self.assertEqual(self.queue.default_stage_id, stage.pk)


def test_work_stage_resource_identity_on_native_composition(tmp_path: Path) -> None:
    run_composed_tests(
        tmp_path, "tests.test_work_identity.WorkStageResourceIdentityTests", app="angee.work",
    )
