"""Abstract decision sources; one decision is one seat's retained question."""

from collections.abc import Iterator
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.deletion import Collector, ProtectedError, RestrictedError
from django.utils.text import capfirst
from rebac import system_context

from angee.base.fields import StateField
from angee.base.impl import ImplClassField
from angee.base.models import AngeeDataModel
from angee.base.refs import RecordRefMixin
from angee.base.scoping import system_queryset
from angee.decisions.managers import DecisionEvidenceManager, DecisionGroupManager, DecisionManager
from angee.decisions.policies import DecisionPolicy
from angee.decisions.states import OPEN_DECISION, ClosedReason, Verdict


class DecisionGroup(AngeeDataModel):
    """A policy and its seats; waiter references point here from their own addon."""

    runtime = True
    sqid_prefix = "dcg_"
    policy = ImplClassField(
        base_class=DecisionPolicy, registry_setting="ANGEE_DECISION_POLICY_CLASSES", default="first",
    )
    issuer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reasked_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reasked_by",
    )
    settled_at = models.DateTimeField(null=True, blank=True)
    objects = DecisionGroupManager()

    class Meta:
        """Compose the group as a retained permission resource."""

        abstract = True
        rebac_resource_type = "decisions/group"

    def rounds(self) -> Iterator[Any]:
        """Yield this round and every retained earlier round, newest first."""
        group: DecisionGroup | None = self
        while group is not None:
            yield group
            group = (system_queryset(type(self)).get(pk=group.reasked_from_id)
                     if group.reasked_from_id is not None else None)

    def is_settled_by(self, decisions: list[Any]) -> bool:
        """Delegate settlement, including unanswered closure, to the selected policy."""
        policy = self._meta.get_field("policy").resolve_for(self)
        return policy.settled(decisions)

    @property
    def outcome(self) -> str | None:
        """Return expired, superseded or canceled; None leaves answer handling to the waiter."""
        if self.settled_at is None:
            return None
        reason = (system_queryset(self.decisions.model).filter(group_id=self.pk).unanswered()
                  .order_by("index").values_list("closed_reason", flat=True).first())
        return "expired" if reason in (ClosedReason.EXPIRED, ClosedReason.INVALID_ATTEMPTS) else reason

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Admit immutable group facts; only the settle verb can set its final timestamp."""
        if not self._state.adding or self.settled_at is not None:
            raise ValidationError("Use group manager verbs; group facts and settlement cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Lock the group before collector cascades, preserving native delete authorization."""
        with transaction.atomic():
            with type(self).objects.hold(self.pk) as group:
                if not group.is_deletable:
                    raise ProtectedError("Only settled, unreferenced decision groups can be deleted.", [self])
            # The outer transaction retains the lock after the system context ends.
            return super().delete(*args, **kwargs)

    @property
    def is_deletable(self) -> bool:
        """Settled, unreferenced groups may go; protected answers remain retained."""
        if self.settled_at is None:
            return False
        try:
            collector = Collector(using=self._state.db or "default")
            collector.collect([self])
        except (ProtectedError, RestrictedError):
            return False
        with system_context(reason="decisions.retention_check"):
            records = [record for rows in collector.data.values() for record in rows]
            if apps.get_model("decisions", "DecisionEvidence").objects.for_records(records).exists():
                return False
        return True


class Decision(RecordRefMixin, AngeeDataModel):
    """One immutable question and its conditional, final answer."""

    runtime = True
    sqid_prefix = "dcn_"
    group = models.ForeignKey("decisions.DecisionGroup", on_delete=models.CASCADE, related_name="decisions")
    index = models.PositiveIntegerField()
    kind = models.CharField(max_length=200)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+")
    assignees = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="+")
    subject_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, blank=True)
    subject_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_content_type", "subject_object_id")
    form_schema = models.JSONField()
    basis = models.JSONField(default=dict)
    context = models.JSONField(default=dict)
    errors = models.JSONField(default=dict)
    verdict = StateField(choices_enum=Verdict, default=Verdict.PENDING, db_index=False)
    closed_reason = StateField(choices_enum=ClosedReason, null=True, blank=True, db_index=False)
    superseded_by = models.ForeignKey("decisions.Decision", on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="+")
    supersede = models.BooleanField(default=False)
    resolution = models.JSONField(default=dict)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    invalid_attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField()
    expires_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)
    objects = DecisionManager()

    class Meta:
        """Constrain retained answers and exclusive open superseding questions."""

        abstract = True
        rebac_resource_type = "decisions/decision"
        constraints = [
            models.UniqueConstraint(fields=("kind", "subject_content_type", "subject_object_id"),
                condition=OPEN_DECISION & models.Q(supersede=True), name="decisions_open_subject_unique"),
            models.UniqueConstraint(fields=("group", "index"), name="decisions_group_index"),
            models.CheckConstraint(condition=models.Q(max_attempts__gt=0), name="decisions_positive_attempts"),
            models.CheckConstraint(condition=(
                models.Q(verdict=Verdict.PENDING, resolved_at__isnull=True, resolved_by__isnull=True)
                & (models.Q(closed_reason__isnull=True) | models.Q(closed_reason__in=[
                    reason for reason in ClosedReason.values if reason != ClosedReason.RESOLVED
                ]))
            ) | models.Q(verdict__in=[v for v in Verdict.values if v != Verdict.PENDING],
                         resolved_at__isnull=False, resolved_by__isnull=False,
                         closed_reason__isnull=False, closed_reason=ClosedReason.RESOLVED),
                name="decisions_resolution_consistent"),
        ]
        indexes = [
            models.Index(fields=("expires_at",), condition=OPEN_DECISION, name="decisions_open_expiry"),
            models.Index(fields=("kind", "subject_content_type", "subject_object_id"), name="decisions_subject"),
        ]

    @property
    def is_pending(self) -> bool:
        """Return whether this snapshot still needs an answer or an expiry transition."""
        return self.verdict == Verdict.PENDING and self.closed_reason is None

    @property
    def is_open(self) -> bool:
        """Use the queryset's live answerability, including unswept deadlines."""
        if "_is_open" in self.__dict__:
            return bool(self.__dict__["_is_open"])
        return system_queryset(type(self)).open().filter(pk=self.pk).exists()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Admit new rows; retained questions change only through manager verbs."""
        if not self._state.adding:
            raise ValidationError("Use decision manager verbs; retained questions and answers cannot be edited.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Identify a seat by the same kind label shown in its inbox."""
        return self.kind_label

    @property
    def kind_label(self) -> str:
        """Present the authored kind without exposing identifier separators."""
        return capfirst(self.kind.replace("_", " ").replace("-", " "))

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Retain individual seats until their owning group can be deleted."""
        raise ValidationError("Delete the settled decision group, not an individual decision.")


class DecisionEvidence(RecordRefMixin, AngeeDataModel):
    """Indexed projection of context references, authored only at admission."""

    runtime = True
    sqid_prefix = "dce_"
    decision = models.ForeignKey("decisions.Decision", on_delete=models.CASCADE, related_name="evidence")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")
    objects = DecisionEvidenceManager()

    class Meta:
        """Index the canonical evidence identity once per decision."""

        abstract = True
        rebac_resource_type = "decisions/evidence"
        constraints = [models.UniqueConstraint(fields=("decision", "content_type", "object_id"),
                                               name="decisions_evidence_unique")]
        indexes = [models.Index(fields=("content_type", "object_id"), name="decisions_evidence_record")]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Admit evidence once; its retained target cannot be rewritten."""
        if not self._state.adding:
            raise ValidationError("Decision evidence cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Retain evidence until its owning group can be deleted."""
        raise ValidationError("Decision evidence cannot be deleted independently.")
