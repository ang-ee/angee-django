"""Checklists: the items a model declares and the results recorded against them.

A model composing :class:`ChecklistMixin` declares its :meth:`~ChecklistMixin.checklist`,
ordered categories and items that composed donors extend through ``super()``,
and the content token its results are compared with
(:meth:`~ChecklistMixin.checklist_revision_expression`). A :class:`ChecklistResult`
is one append-only outcome for one item on one record: its status, its detail and
the record's token when it was recorded. Its evidence is one
:class:`ChecklistEvidence` link per record that produced it.

**Read.** A result shows when its record is readable and every one of its
evidence links is readable (``checklists/result#read`` in ``permissions.zed``).
The addon that owns a checklist type declares the type's ``record``-backed
relation on ``checklists/result``; the addon that records results declares
each evidence type it uses on ``checklists/evidence``. The latest result per
item wins, so an unreadable latest result shows nothing, never an older
readable one.

**Status.** Results are recorded, never derived: computable readiness stays a
verb-eligibility condition. One SQL rule assesses items, categories and the
record (:class:`angee.checklists.managers.ChecklistStatuses`); cells, filters,
counts and the record projection read it. A content change, as the model's
token defines it, makes passed and not-applicable results unresolved; risks
and conflicts stay visible. A result whose key left the checklist still counts
while it is a risk, a conflict or unresolved.
"""

from __future__ import annotations

from collections.abc import Iterable
from operator import attrgetter
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.db import models
from rebac.field_backing import canonical_model
from rebac.resources import model_resource_type

from angee.base.fields.state import StateField
from angee.base.models.append_only import AppendOnlyModel
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.derived import DerivedFrom
from angee.base.models.merge_references import MergePolicy
from angee.base.models.refs import RecordRefMixin
from angee.base.permissions import declared_rebac_definition
from angee.base.refs import RecordRef, record_view_refs
from angee.checklists.contracts import Checklist, ChecklistStatus
from angee.checklists.managers import (
    ChecklistAssessment,
    ChecklistEvidenceManager,
    ChecklistResultManager,
    LatestResults,
)


class ChecklistMixin(models.Model):
    """A model whose rows carry a declared checklist and its recorded results.

    Compose it before the model base, on the topmost REBAC-typed MTI ancestor
    the results key on, and declare the type's ``record``-backed relation on
    ``checklists/result`` with its ``target_read`` and ``target_write`` arms
    (:meth:`ChecklistResult.check`). A child may declare the checklist the
    ancestor's rows carry when the child owns the content: the hooks are read on
    the queried model.
    """

    checklist_results = GenericRelation(
        "checklists.ChecklistResult",
        content_type_field="content_type",
        object_id_field="object_id",
    )
    _checklist_latest = LatestResults()
    """Each listed row's latest readable result per item, for a page's projection."""

    class Meta:
        """Abstract checklist composition."""

        abstract = True

    @classmethod
    def checklist(cls) -> Checklist:
        """Return this model's checklist; donors extend it through ``super()``."""

        return Checklist()

    @classmethod
    def checklist_revision_expression(cls) -> models.Expression | None:
        """Return the record's content token as an SQL expression over its own rows.

        Results are compared with it by equality: an opaque string covering the
        content the checks read, excluding lifecycle and audit stamps. A model with
        a non-empty checklist must declare one (``checklists.E005``).
        """

        return None

    def checklist_record(
        self,
        key: str,
        status: str,
        detail: str = "",
        *,
        evidence: Iterable[models.Model] = (),
    ) -> ChecklistResult:
        """Record one result for item ``key`` under the ambient actor; see :meth:`ChecklistResultManager.record`."""

        return _result_model().objects.record(self, key, status, detail, evidence=evidence)

    def checklist_assessment(self) -> ChecklistAssessment:
        """Assess this row's checklist for the ambient actor, as its list cells and filters do."""

        return _result_model().objects.assess(self)


def _result_model() -> type[ChecklistResult]:
    model = apps.get_model("checklists", "ChecklistResult")
    assert issubclass(model, ChecklistResult)
    return model


class ChecklistResult(AppendOnlyModel, AuditMixin, RecordRefMixin, AngeeDataModel, concrete=True):
    """One recorded outcome of one checklist item on one record.

    The ``record`` generic foreign key stores :func:`rebac.generic_target`; the
    target type's addon declares the relation authorizing it
    (``permissions.zed``). ``revision`` is the record's content token when the
    result was recorded. Rows are append-only: the latest row per item is its
    result. Merging the record keeps the rows on the merged record; deleting the
    record deletes them through its ``checklist_results`` relation.
    """

    sqid_prefix = "ckr_"
    merge_policy = MergePolicy.KEEP
    on_target_delete = models.CASCADE

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")
    key = models.CharField(max_length=100)
    status = StateField(choices_enum=ChecklistStatus, status_display="pill")
    detail = models.TextField(blank=True, default="")
    revision = models.CharField(max_length=255, blank=True, default="")

    objects = ChecklistResultManager()

    class Meta:
        """Django model options for a checklist result."""

        abstract = True
        ordering = ("-created_at", "-id")
        rebac_resource_type = "checklists/result"
        indexes = (models.Index(fields=("content_type", "object_id", "key", "created_at")),)

    def __str__(self) -> str:
        """Return a readable label for Django displays."""

        return f"{self.key}: {self.status}"

    def change_related_records(self) -> tuple[RecordRef, ...]:
        """A recorded result changes its record's reads, including the views its record inherits."""

        return record_view_refs(self.record_ref)

    @classmethod
    def teardown_for_target(cls, target: models.Model) -> None:
        """Leave a deleted record's results to its ``checklist_results`` cascade.

        The record's reverse relation collects its results, and their evidence
        links follow through their foreign key; the append-only queryset never
        deletes them in bulk.
        """

        del target

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Require declared checklists, and the edge's declared target types, to agree.

        A checklist type composes :class:`ChecklistMixin` on its canonical model
        and declares a ``record``-backed relation for it; each declared target
        composes the mixin. Each model with items declares unique keys, known
        categories and a content token. Compiled declarations are read, so the
        check also runs before database provisioning.
        """

        errors = super().check(**kwargs)
        owners: set[type[models.Model]] = set()
        for model in sorted(cls._meta.apps.get_models(), key=attrgetter("_meta.label")):
            if not issubclass(model, ChecklistMixin):
                continue
            errors.extend(_checklist_errors(model))
            owner = canonical_model(model)
            if owner is not None and issubclass(owner, ChecklistMixin):
                owners.add(owner)
                continue
            errors.append(
                checks.Error(
                    f"{model._meta.label} composes ChecklistMixin, but its results key on "
                    + (f"{owner._meta.label}, which does not." if owner else "no REBAC type."),
                    hint="Compose ChecklistMixin on the topmost REBAC-typed MTI ancestor.",
                    obj=model,
                    id="checklists.E001",
                )
            )
        declared = set(cls.declared_target_models(definition_for=declared_rebac_definition).values())
        for owner in sorted(owners - declared, key=attrgetter("_meta.label")):
            errors.append(
                checks.Error(
                    f"{owner._meta.label} composes ChecklistMixin, but {model_resource_type(cls)} declares no "
                    f"relation for {model_resource_type(owner)}.",
                    hint="Declare the type's `record`-backed relation and its target_read and target_write "
                    "arms in the owning addon's permissions.extends.zed.",
                    obj=owner,
                    id="checklists.E002",
                )
            )
        for target in sorted(declared - owners, key=attrgetter("_meta.label")):
            errors.append(
                checks.Error(
                    f"{model_resource_type(cls)} declares a relation for {model_resource_type(target)}, but "
                    f"{target._meta.label} does not compose ChecklistMixin.",
                    hint="Compose ChecklistMixin on the model, or remove the relation.",
                    obj=cls,
                    id="checklists.E003",
                )
            )
        return errors


def _checklist_errors(model: type[ChecklistMixin]) -> list[checks.CheckMessage]:
    """Validate one model's composed checklist declaration."""

    if model._meta.abstract:
        return []
    checklist = model.checklist()
    errors: list[checks.CheckMessage] = [
        checks.Error(
            f"{model._meta.label}.checklist(): {problem}.",
            hint="Give every item and category a unique key, and every item a declared category.",
            obj=model,
            id="checklists.E004",
        )
        for problem in checklist.problems()
    ]
    if checklist.items and model.checklist_revision_expression() is None:
        errors.append(
            checks.Error(
                f"{model._meta.label} declares checklist items but no checklist_revision_expression().",
                hint="Return the record's content token, the string its results are compared with.",
                obj=model,
                id="checklists.E005",
            )
        )
    return errors


class ChecklistEvidence(DerivedFrom, concrete=True):
    """One retained link from a checklist result to a record that produced it.

    The recording addon declares each evidence type on ``checklists/evidence``
    with a ``record``-backed relation unioned into ``record_read``. A link
    whose record the reader cannot read hides its result; a link of an
    undeclared type is refused when the result is recorded.

    Deleting an evidence record deletes every result it supports, whatever its
    other links: the result's read depended on all of them, so a result that
    lost one must not become readable to more people. The item then reads as
    it did before the result was recorded.
    """

    sqid_prefix = "cke_"
    on_target_delete = models.CASCADE

    result = models.ForeignKey("checklists.ChecklistResult", on_delete=models.CASCADE, related_name="evidence")

    objects = ChecklistEvidenceManager()

    class Meta:
        """Django model options for a checklist evidence link."""

        abstract = True
        ordering = ("id",)
        rebac_resource_type = "checklists/evidence"
        indexes = (models.Index(fields=("content_type", "object_id")),)
        constraints = (
            models.UniqueConstraint(
                fields=("result", "content_type", "object_id"),
                name="%(app_label)s_evidence_unique",
            ),
        )

    def __str__(self) -> str:
        """Return a readable label for Django displays."""

        return f"{self.result_id}<-{self.content_type_id}:{self.object_id}"

    @classmethod
    def teardown_for_target(cls, target: models.Model) -> None:
        """Delete the results ``target`` is evidence of; their links follow through the foreign key.

        Append-only rows leave through their owner's delete, never the collection's.
        """

        supported = cls._base_manager.filter(cls.record_filter(target)).values("result_id")
        for result in _result_model()._base_manager.filter(pk__in=supported).order_by("pk"):
            result._owner_delete()
