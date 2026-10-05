"""One question, its immutable proposal, and its final verdict."""

from typing import Any

from django.conf import settings
from django.db import models
from django.utils.text import capfirst
from rebac import system_context

from angee.base.evidence import DerivedFrom
from angee.base.mixins import AppendOnlyModel, OptimisticLockMixin
from angee.base.models import AngeeDataModel
from angee.base.scoping import system_queryset
from angee.decisions.managers import DecisionManager, DecisionRecordManager
from angee.graphql.events import ChangeRelatedRecord


class Decision(OptimisticLockMixin, AppendOnlyModel, AngeeDataModel):
    """An immutable question; answering records choices without applying them."""

    runtime = True
    sqid_prefix = "dcn_"
    kind = models.CharField(max_length=200)
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    assignees = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="+")
    proposal = models.JSONField()
    context = models.JSONField(default=dict)
    verdict = models.JSONField(null=True, blank=True)
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    answered_at = models.DateTimeField(null=True, blank=True)
    objects = DecisionManager()

    def change_related_records(self) -> tuple[ChangeRelatedRecord, ...]:
        """Publish the question's retained concern identities with its verdict."""
        with system_context(reason="decisions.change_concerns"):
            links = system_queryset(self.records.model).filter(decision_id=self.pk)
            return tuple(dict.fromkeys(reference for link in links
                                       for reference in ChangeRelatedRecord.for_record(link.record_ref)))

    class Meta:
        abstract = True
        rebac_resource_type = "decisions/decision"
        constraints = [models.CheckConstraint(
            condition=models.Q(verdict__isnull=True, answered_by__isnull=True, answered_at__isnull=True)
            | models.Q(verdict__isnull=False, answered_by__isnull=False, answered_at__isnull=False),
            name="decisions_verdict_consistent",
        )]

    @property
    def is_open(self) -> bool:
        """No verdict means the question is still open."""
        return self.verdict is None

    @property
    def kind_label(self) -> str:
        return capfirst(self.kind.replace("_", " ").replace("-", " "))

    @property
    def verdict_label(self) -> str:
        """Read the chosen labels from this question's frozen alternatives."""
        if self.verdict is None:
            return ""
        if not self.verdict:
            return "Withdrawn"
        return "; ".join(alternative["label"] for alternative in self.proposal["alternatives"]
                         if alternative["key"] in self.verdict)

    def __str__(self) -> str:
        return self.kind_label

    def decide(self, *, actor: Any, chosen: list[str], revision: int | None = None) -> Any:
        return type(self).objects.decide(self, actor=actor, chosen=chosen, revision=revision)

    def validate_verdict(self, chosen: tuple[Any, ...], *, actor: Any) -> None:
        """Allow composed policy to refuse an answer before its verdict is retained."""


class DecisionRecord(DerivedFrom):
    """A concern link: decision, canonical content type and object id, with no payload."""

    runtime = True
    sqid_prefix = "dcr_"
    decision = models.ForeignKey("decisions.Decision", on_delete=models.CASCADE, related_name="records")
    objects = DecisionRecordManager()

    class Meta:
        abstract = True
        rebac_resource_type = "decisions/record"
        constraints = [
            models.UniqueConstraint(fields=("decision", "content_type", "object_id"), name="decisions_record_unique")
        ]
        indexes = [models.Index(fields=("content_type", "object_id"), name="decisions_record_target")]
