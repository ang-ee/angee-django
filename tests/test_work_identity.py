"""Work stage identities and resource pointers use their canonical owners."""

from pathlib import Path

from django.apps import apps
from rebac import system_context

from angee.base.fields.identity import SqidField
from angee.work.models import Stage
from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


def test_work_stage_prefix_is_canonical_and_rejects_foreign_prefixes() -> None:
    foreign = SqidField(real_field_name="id", prefix="other_", min_length=8)
    field = Stage._meta.get_field("sqid")
    assert isinstance(field, SqidField)
    assert Stage.public_id_from_pk(29).startswith("wst_")
    assert field.public_id_to_value(Stage.public_id_from_pk(29)) == 29
    assert field.public_id_to_value(foreign.public_id_from_value(29)) is None


class WorkStageResourceIdentityTests(WorkCase):
    def test_xref_resolves_the_native_stage_and_keeps_queue_pointer(self):
        resource = apps.get_model("resources", "Resource")
        stage = self.stages["Ready"]
        with system_context(reason="test.work.stage_identity"):
            self.queue.default_stage = stage
            self.queue.save(update_fields=["default_stage", "updated_at"])
            ledger = resource.objects.create(
                source_addon="angee.work", source_path="resources/stage-identity.yaml", tier="demo",
                xref="stage.identity", content_hash="identity", target_model="work.Stage",
                target_id=stage.sqid,
            )
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
