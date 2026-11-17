"""A checklist type and an evidence type for the checklists tests."""

from __future__ import annotations

from django.db import models
from django.db.models.functions import Concat

from angee.base.expressions import TextSHA256
from angee.base.models.base import AngeeDataModel
from angee.checklists.contracts import Checklist, ChecklistCategory, ChecklistItem
from angee.checklists.models import ChecklistMixin


class Inspection(ChecklistMixin, AngeeDataModel):
    """A record whose name and notes the checklist reads; its ``state`` is lifecycle only."""

    sqid_prefix = "xin_"
    name = models.CharField(max_length=100)
    notes = models.TextField(blank=True, default="")
    state = models.CharField(max_length=20, default="open")

    class Meta(AngeeDataModel.Meta):
        abstract = False
        db_table = "test_checklistdemo_inspection"
        rebac_resource_type = "checklistdemo/inspection"

    @classmethod
    def checklist(cls) -> Checklist:
        return (
            super()
            .checklist()
            .extend(
                categories=(
                    ChecklistCategory("identity", "Identity"),
                    ChecklistCategory("content", "Content"),
                    ChecklistCategory("archive", "Archive"),
                ),
                items=(
                    ChecklistItem("owner", "Owner known", category="identity", blocking=True),
                    ChecklistItem("signature", "Signature present", category="identity"),
                    ChecklistItem("totals", "Totals agree", category="content", blocking=True),
                    ChecklistItem("marker", "Printed marker", category="content"),
                ),
            )
        )

    @classmethod
    def checklist_revision_expression(cls) -> models.Expression:
        return TextSHA256(
            Concat(models.F("name"), models.Value("\x1f"), models.F("notes"), output_field=models.TextField())
        )


class Report(AngeeDataModel):
    """A record that produces checklist results, with its own readers."""

    sqid_prefix = "xrp_"
    name = models.CharField(max_length=100)

    class Meta(AngeeDataModel.Meta):
        abstract = False
        db_table = "test_checklistdemo_report"
        rebac_resource_type = "checklistdemo/report"


class CraneInspection(Inspection):
    """A multi-table child that declares further items on the rows its parent carries, as a document child does."""

    lift_rating = models.CharField(max_length=20, default="")

    class Meta:
        db_table = "test_checklistdemo_crane_inspection"
        rebac_resource_type = "checklistdemo/crane_inspection"

    @classmethod
    def checklist(cls) -> Checklist:
        return super().checklist().extend(items=(ChecklistItem("load", "Load rating", category="content"),))


class Notice(ChecklistMixin, AngeeDataModel):
    """A checklist type anyone may read, signed in or not; its writers record."""

    sqid_prefix = "xnt_"
    text = models.TextField(default="")

    class Meta(AngeeDataModel.Meta):
        abstract = False
        db_table = "test_checklistdemo_notice"
        rebac_resource_type = "checklistdemo/notice"

    @classmethod
    def checklist(cls) -> Checklist:
        return super().checklist().extend(items=(ChecklistItem("wording", "Wording reviewed"),))

    @classmethod
    def checklist_revision_expression(cls) -> models.Expression:
        return TextSHA256(models.F("text"))
