"""Merge capabilities add no duplicate fields to native Angee compositions."""

from django.db import models
from django.db.migrations.state import ModelState
from django.test.utils import isolate_apps

from angee.base.models.base import AngeeDataModel
from angee.base.models.merge import MergeableMixin
from angee.base.models.merge_references import ForeignKeyReference, MergeContractMixin, MergePolicy
from angee.base.models.trash import ArchiveMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryMergeableUnscopedManager


def test_merge_base_preserves_existing_composed_field_state_and_mro() -> None:
    with isolate_apps():
        class LegacyMergeable(models.Model):
            merged_into = models.ForeignKey(
                "self", null=True, blank=True, editable=False,
                on_delete=models.PROTECT, related_name="merged_from",
            )

            class Meta:
                abstract = True

        class Legacy(HistoryMixin, LegacyMergeable, ArchiveMixin, AngeeDataModel):
            class Meta:
                abstract = True
                app_label = "merge_contract"

        class Composed(HistoryMixin, MergeableMixin, ArchiveMixin, AngeeDataModel):

            _angee_base = AngeeHistoryMergeableUnscopedManager()
            class Meta:
                abstract = True
                app_label = "merge_contract"

        before = ModelState.from_model(Legacy)
        after = ModelState.from_model(Composed)
        assert {
            name: field.deconstruct()[1:] for name, field in before.fields.items()
        } == {
            name: field.deconstruct()[1:] for name, field in after.fields.items()
        }
        assert before.options == after.options
        assert [base for base in Legacy.__mro__[1:] if base is not LegacyMergeable] == [
            base for base in Composed.__mro__[1:] if base is not MergeableMixin
        ]


def test_reference_contracts_continue_to_accept_plain_django_models() -> None:
    with isolate_apps():
        class Target(models.Model):
            class Meta:
                app_label = "merge_contract"

        class Link(MergeContractMixin):
            target = models.ForeignKey(Target, on_delete=models.PROTECT)

            class Meta:
                app_label = "merge_contract"

        field = Link._meta.get_field("target")
        assert isinstance(field, models.ForeignKey)
        reference = ForeignKeyReference(Link, MergePolicy.MOVE, field)
        row = Target(pk=7)
        assert reference.holds(row)
        assert reference.lookup(row) == {"target_id": 7}
        assert reference.columns == ("target",)
        assert not issubclass(Link, AngeeDataModel)


def test_real_foreign_key_on_generic_edge_always_blocks_creation_undo():
    from django.contrib.contenttypes.fields import GenericForeignKey

    from angee.base.models.refs import RecordRefMixin

    with isolate_apps():
        class Target(models.Model):
            class Meta:
                app_label = "merge_contract"

        class Edge(RecordRefMixin):
            target = models.ForeignKey(Target, on_delete=models.CASCADE)
            content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
            object_id = models.PositiveIntegerField()
            content_object = GenericForeignKey()
            on_target_delete = models.CASCADE

            @classmethod
            def blocks_target_delete(cls, target):
                return False

            class Meta:
                app_label = "merge_contract"

        reference = ForeignKeyReference(Edge, MergePolicy.MOVE, Edge._meta.get_field("target"))
        assert reference.blocks_target_delete(Target)
