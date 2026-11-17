"""Recording checklist results and the one SQL rule that assesses them.

:class:`ChecklistStatuses` is the status rule. A derived input computes, once
per record row, the record's content token and whether the reader holds the
record's ``target_read``. For each declared item it then reads the latest
structural result row; the row counts only when the reader may read the record
and no evidence link hides the row (``checklists/result#evidence_hidden``), so an
unreadable latest result reads ``not_checked`` and never falls back to an older
one. A passed or not-applicable result recorded against another content token,
or against a record whose token is NULL, reads ``unresolved``. Categories and the
record combine their items, and a result whose key left the checklist still
counts while it needs attention. PostgreSQL keeps each derived input unflattened
(``OFFSET 0``), so the token, the record's read and each item are evaluated once.

Cells, filters, counts and :meth:`ChecklistResultManager.assess` all read that
expression; the Python projection only adds each item's recorded detail,
evidence and time, chosen by the statuses the SQL returned.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Self, cast

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Coalesce, JSONObject
from django.db.models.lookups import Exact, In, IsNull
from rebac import current_actor, generic_target, system_context
from rebac.actors import is_sudo
from rebac.field_backing import canonical_model
from rebac.relation_loading import relation_actor

from angee.base.expressions import JSONArraySubquery
from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyQuerySet
from angee.base.models.identity import public_id_for
from angee.base.permissions import reader_queryset, require_rebac_queryset
from angee.base.prefetch import InstancePrefetch
from angee.base.querysets import AngeeQuerySet
from angee.checklists.contracts import ATTENTION_STATUSES, PASSING_STATUSES, Checklist, ChecklistStatus
from angee.provenance.evidence import EvidenceReference, readable_records

if TYPE_CHECKING:
    from django.db.backends.base.base import BaseDatabaseWrapper
    from django.db.models.sql.compiler import SQLCompiler, _AsSqlType

    from angee.checklists.models import ChecklistEvidence, ChecklistMixin, ChecklistResult


NO_RESULT_DETAIL = "No check result is available."
LATEST_ORDER = ("-created_at", "-id")
"""The order whose first row per item is its result: append order, which the primary key breaks ties in."""


def _mixin() -> type[ChecklistMixin]:
    from angee.checklists.models import ChecklistMixin

    return ChecklistMixin


def _result_model() -> type[ChecklistResult]:
    return cast("type[ChecklistResult]", apps.get_model("checklists", "ChecklistResult"))


def _evidence_model() -> type[ChecklistEvidence]:
    return cast("type[ChecklistEvidence]", apps.get_model("checklists", "ChecklistEvidence"))


def _target_content_type(model: type[models.Model]) -> ContentType:
    """The content type a model's results key on: its topmost REBAC-typed ancestor's."""

    return ContentType.objects.get_for_model(canonical_model(model) or model)


def _holding(actor: Any, permission: str) -> models.QuerySet[Any] | None:
    """The results on which ``actor`` holds ``permission``, as a subquery-ready aggregate scope.

    ``None`` under a named system context, which reads every result. Without an
    actor or system context the scope fails closed, as REBAC's ambient read does.
    """

    if actor is None and is_sudo():
        return None
    rows = (
        require_rebac_queryset(_result_model())
        if actor is None
        else require_rebac_queryset(
            _result_model(),
            actor=actor,
        )
    )
    return cast(Any, rows.with_action(permission)).scoped_for_aggregate()


class _InputColumn(models.Expression):
    """One column of the assessment's own derived input, read by the expressions it nests."""

    def __init__(self, table: str, name: str, output_field: models.Field) -> None:
        super().__init__(output_field=output_field)
        self.table = table
        self.name = name

    def as_sql(self, compiler: SQLCompiler, connection: BaseDatabaseWrapper) -> _AsSqlType:
        quote = connection.ops.quote_name
        return f"{quote(self.table)}.{quote(self.name)}", []

    def get_group_by_cols(self) -> list[Any]:
        return []


_INPUT = "checklist_input"
_ITEMS = "checklist_items"
_REVISION = "_checklist_revision"
_READABLE = "_checklist_readable"

type Selection = Literal["status", "vector", "projection", "next_blocking"] | tuple[Literal["category"], str]


class ChecklistStatuses(models.Func):
    """Assess one record row's checklist for ``actor``, in one scalar subquery.

    Build it through :meth:`for_rows`, which reads the reader a record queryset
    carries; ``actor`` ``None`` reads through REBAC's ambient scope.

    ``select`` names the output: ``"status"`` (the record's summary),
    ``("category", key)``, ``"next_blocking"`` (the first blocking item that has
    not passed, or ``NULL``), ``"vector"`` (summary and categories as JSON, for
    counts) or ``"projection"`` (also each item's status and staleness, for the
    record projection).
    """

    def __init__(self, model: type[models.Model], *, actor: Any, select: Selection) -> None:
        if not issubclass(model, _mixin()):
            raise TypeError(f"{model._meta.label} does not compose ChecklistMixin.")
        self.checklist: Checklist = model.checklist()
        self.select = select
        target = canonical_model(model) or model
        self.target = (target._meta.app_label, target._meta.model_name)
        self.target_read = _holding(actor, "target_read")
        self.evidence_hidden = _holding(actor, "evidence_hidden")
        self.items = self.checklist.category_items(select[1]) if isinstance(select, tuple) else self.checklist.items
        self.retired = select in ("status", "vector", "projection")
        self.projection = select == "projection"
        revision = model.checklist_revision_expression()
        if revision is None:
            revision = models.Value("", output_field=models.CharField())
        sources: list[Any] = [revision, self._record_readable(), *(self._item(item.key) for item in self.items)]
        if self.retired:
            sources.append(self._retired_status())
        output: models.Field = (
            models.JSONField()
            if select in ("vector", "projection")
            else models.CharField(null=select == "next_blocking")
        )
        super().__init__(*sources, output_field=output)

    @classmethod
    def for_rows(cls, rows: models.QuerySet[Any], select: Selection) -> ChecklistStatuses:
        """Assess ``rows``' records for the reader the queryset carries."""

        return cls(rows.model, actor=relation_actor(rows), select=select)

    def _results(self, record: str = "pk") -> Any:
        """The record's results, correlated by its content type's natural key so building reads no rows."""

        app_label, model_name = self.target
        return _result_model()._base_manager.filter(
            content_type__app_label=app_label,
            content_type__model=model_name,
            object_id=models.OuterRef(record),
        )

    def _record_readable(self) -> models.Exists | models.Value:
        """Whether the reader holds the record's ``target_read``: every result of a record shares it."""

        if self.target_read is None:
            return models.Value(True, output_field=models.BooleanField())
        app_label, model_name = self.target
        return models.Exists(
            self.target_read.filter(
                content_type__app_label=app_label,
                content_type__model=model_name,
                object_id=models.OuterRef("pk"),
            )
        )

    def _shown(self) -> models.Q:
        """A result row the reader may read: the record is readable and no evidence link hides the row."""

        shown = models.Q(_InputColumn(_INPUT, _READABLE, models.BooleanField()))
        if self.evidence_hidden is not None:
            shown &= ~models.Q(models.Exists(self.evidence_hidden.filter(pk=models.OuterRef("pk"))))
        return shown

    def _fresh(self) -> models.Q:
        """Recorded against the record's current token; a NULL token is never current."""

        token = _InputColumn(_INPUT, _REVISION, models.CharField())
        return models.Q(Exact(models.F("revision"), token)) & models.Q(IsNull(token, False))

    def _value(self, status: models.Expression | models.F, stale: bool) -> models.Expression | models.F:
        if not self.projection:
            return status
        return JSONObject(status=status, stale=models.Value(stale, output_field=models.BooleanField()))

    def _item(self, key: str) -> models.Expression:
        """One item's status (and, for the projection, its staleness) from its latest row, in one subquery."""

        not_checked = self._value(models.Value(ChecklistStatus.NOT_CHECKED), False)
        output: models.Field = models.JSONField() if self.projection else models.CharField()
        item = models.Case(
            models.When(~self._shown(), then=not_checked),
            models.When(
                models.Q(status__in=PASSING_STATUSES) & ~self._fresh(),
                then=self._value(models.Value(ChecklistStatus.UNRESOLVED), True),
            ),
            models.When(~self._fresh(), then=self._value(models.F("status"), True)),
            default=self._value(models.F("status"), False),
            output_field=output,
        )
        latest = self._results().filter(key=key).annotate(_item=item).order_by(*LATEST_ORDER)
        return Coalesce(models.Subquery(latest.values("_item")[:1]), not_checked, output_field=output)

    def _retired_status(self) -> models.Subquery:
        """The strongest attention status among the readable latest results of retired keys."""

        latest = (
            self._results("object_id")
            .exclude(key__in=self.checklist.keys)
            .first_per_group("key", order_by=LATEST_ORDER, limit=1)
        )
        strength = models.Case(
            *(models.When(status=status, then=models.Value(rank)) for rank, status in enumerate(ATTENTION_STATUSES)),
            output_field=models.IntegerField(),
        )
        alerting = (
            self._results()
            .exclude(key__in=self.checklist.keys)
            .filter(self._shown(), status__in=ATTENTION_STATUSES, pk__in=latest.values("pk"))
            .annotate(_strength=strength)
            .order_by("_strength")
        )
        return models.Subquery(alerting.values("status")[:1], output_field=models.CharField(null=True))

    def _columns(self) -> tuple[list[models.Expression], _InputColumn | None, list[models.Expression]]:
        """Each item's status and staleness as the combining expressions read them, and the retired status."""

        statuses: list[models.Expression] = []
        stale: list[models.Expression] = []
        for index in range(len(self.items)):
            if self.projection:
                column = _InputColumn(_ITEMS, f"i{index}", models.JSONField())
                statuses.append(KeyTextTransform("status", column))
                stale.append(KeyTransform("stale", column))
            else:
                statuses.append(_InputColumn(_ITEMS, f"i{index}", models.CharField()))
        retired = _InputColumn(_ITEMS, "r", models.CharField(null=True)) if self.retired else None
        return statuses, retired, stale

    def _summary(self, items: Sequence[models.Expression], retired: _InputColumn | None) -> models.Expression:
        conditions = []
        for attention in ATTENTION_STATUSES:
            condition = models.Q(Exact(retired, models.Value(attention))) if retired is not None else models.Q()
            for item in items:
                condition |= models.Q(Exact(item, models.Value(attention)))
            if condition:
                conditions.append(models.When(condition, then=models.Value(attention)))
        if not items:
            return models.Case(
                *conditions,
                default=models.Value(ChecklistStatus.NOT_CHECKED),
                output_field=models.CharField(),
            )
        unchecked = [models.Q(Exact(item, models.Value(ChecklistStatus.NOT_CHECKED))) for item in items]
        all_unchecked = unchecked[0]
        none_unchecked = ~unchecked[0]
        for condition in unchecked[1:]:
            all_unchecked &= condition
            none_unchecked &= ~condition
        return models.Case(
            *conditions,
            models.When(all_unchecked, then=models.Value(ChecklistStatus.NOT_CHECKED)),
            models.When(none_unchecked, then=models.Value(ChecklistStatus.PASSED)),
            default=models.Value(ChecklistStatus.UNRESOLVED),
            output_field=models.CharField(),
        )

    @staticmethod
    def _category(items: Sequence[models.Expression]) -> models.Expression:
        if not items:
            return models.Value(ChecklistStatus.NOT_APPLICABLE, output_field=models.CharField())
        conditions = []
        for value in (*ATTENTION_STATUSES, ChecklistStatus.NOT_CHECKED):
            condition = models.Q(Exact(items[0], models.Value(value)))
            for item in items[1:]:
                condition |= models.Q(Exact(item, models.Value(value)))
            conditions.append(models.When(condition, then=models.Value(value)))
        all_not_applicable = models.Q(Exact(items[0], models.Value(ChecklistStatus.NOT_APPLICABLE)))
        for item in items[1:]:
            all_not_applicable &= models.Q(Exact(item, models.Value(ChecklistStatus.NOT_APPLICABLE)))
        return models.Case(
            *conditions,
            models.When(all_not_applicable, then=models.Value(ChecklistStatus.NOT_APPLICABLE)),
            default=models.Value(ChecklistStatus.PASSED),
            output_field=models.CharField(),
        )

    def _categories(self, items: Sequence[models.Expression]) -> dict[str, models.Expression]:
        by_key = dict(zip((item.key for item in self.items), items, strict=True))
        return {
            category.key: self._category([by_key[item.key] for item in self.checklist.category_items(category.key)])
            for category in self.checklist.categories
        }

    def _output(self) -> models.Expression:
        items, retired, stale = self._columns()
        if isinstance(self.select, tuple):
            return self._category(items)
        if self.select == "next_blocking":
            return models.Case(
                *(
                    models.When(~models.Q(In(column, PASSING_STATUSES)), then=models.Value(item.key))
                    for item, column in zip(self.items, items, strict=True)
                    if item.blocking
                ),
                default=models.Value(None),
                output_field=models.CharField(null=True),
            )
        summary = self._summary(items, retired)
        if self.select == "status":
            return summary
        fields: dict[str, Any] = {"status": summary}
        if categories := self._categories(items):
            fields["categories"] = JSONObject(**categories)
        if self.select == "projection" and self.items:
            fields["items"] = JSONObject(**{item.key: column for item, column in zip(self.items, items, strict=True)})
            fields["stale"] = JSONObject(**{item.key: column for item, column in zip(self.items, stale, strict=True)})
        return JSONObject(**fields)

    def as_sql(
        self,
        compiler: SQLCompiler,
        connection: BaseDatabaseWrapper,
        function: str | None = None,
        template: str | None = None,
        arg_joiner: str | None = None,
        **extra_context: Any,
    ) -> _AsSqlType:
        quote = connection.ops.quote_name
        revision, readable, *sources = self.get_source_expressions()
        revision_sql, revision_params = compiler.compile(revision)
        readable_sql, readable_params = compiler.compile(readable)
        names = [f"i{index}" for index in range(len(self.items))]
        if self.retired:
            names.append("r")
        compiled = [compiler.compile(source) for source in sources]
        output_sql, output_params = compiler.compile(self._output().resolve_expression(compiler.query))
        # PostgreSQL otherwise flattens each derived input into its readers and
        # repeats the record's token, its read and every item read per reference.
        keep = " OFFSET 0" if connection.vendor == "postgresql" else ""
        columns = ", ".join(f"{sql} AS {quote(name)}" for (sql, _params), name in zip(compiled, names, strict=True))
        sql = (
            f"(SELECT {output_sql} FROM (SELECT {columns or 'NULL AS ' + quote('_')} FROM "
            f"(SELECT {revision_sql} AS {quote(_REVISION)}, {readable_sql} AS {quote(_READABLE)}{keep}) "
            f"AS {quote(_INPUT)}{keep}) AS {quote(_ITEMS)})"
        )
        params = (
            *output_params,
            *(param for _sql, params in compiled for param in params),
            *revision_params,
            *readable_params,
        )
        return sql, params


@dataclass(frozen=True)
class LatestResult:
    """A record's latest readable result for one item, with the records that produced it.

    Every evidence reference is readable by the reader the result was read for:
    one hidden link would have hidden the result (``checklists/result#read``).
    """

    result: ChecklistResult
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class ChecklistItemAssessment:
    """One item's assessed status with its latest readable result and that result's evidence."""

    key: str
    label: str
    category: str | None
    blocking: bool
    status: str
    detail: str
    stale: bool
    result: ChecklistResult | None = None
    evidence: tuple[EvidenceReference, ...] = ()

    @property
    def recorded_at(self) -> datetime | None:
        return self.result.created_at if self.result is not None else None


@dataclass(frozen=True)
class ChecklistGroupAssessment:
    """One category's assessed status over its items."""

    key: str
    label: str
    status: str
    detail: str
    item_keys: tuple[str, ...]


@dataclass(frozen=True)
class ChecklistAssessment:
    """A record's checklist as its reader sees it: summary, categories and items.

    ``complete`` means every declared item has a readable result; ``stale``
    that a readable current result predates the record's content. Items list
    the declared items in order, then retired keys whose result needs attention.
    """

    status: str
    detail: str
    complete: bool
    stale: bool
    groups: tuple[ChecklistGroupAssessment, ...]
    items: tuple[ChecklistItemAssessment, ...]

    @property
    def recorded_at(self) -> datetime | None:
        times = [item.recorded_at for item in self.items if item.recorded_at is not None]
        return max(times) if times else None

    @property
    def blocking_unmet(self) -> tuple[ChecklistItemAssessment, ...]:
        """The blocking items that have not passed, in declaration order."""

        return tuple(item for item in self.items if item.blocking and item.status not in PASSING_STATUSES)


def _detail(items: Sequence[ChecklistItemAssessment], status: str) -> str:
    """Describe a summary or a category by its status and its items' recorded details."""

    if status == ChecklistStatus.NOT_CHECKED:
        return "One or more check results are unavailable." if items else NO_RESULT_DETAIL
    if status in ATTENTION_STATUSES:
        return next(
            (item.detail for item in items if item.status == status),
            "The complete current checklist has not been recorded.",
        )
    if status == ChecklistStatus.NOT_APPLICABLE:
        return "No applicable checks."
    applicable = sum(item.status != ChecklistStatus.NOT_APPLICABLE for item in items)
    return f"{applicable} applicable check{'s' if applicable != 1 else ''} passed."


def _item_detail(status: str, result: ChecklistResult | None) -> str:
    if result is None:
        return NO_RESULT_DETAIL
    if status != result.status and result.status in PASSING_STATUSES:
        return f"The record changed after this check. Earlier result: {result.detail}"
    return str(result.detail)


def assessment(
    checklist: Checklist, statuses: Mapping[str, Any], latest: Iterable[LatestResult]
) -> ChecklistAssessment:
    """Build a record's assessment from its SQL statuses and its latest readable results."""

    by_key = {entry.result.key: entry for entry in latest}
    item_statuses = statuses.get("items") or {}
    stale = statuses.get("stale") or {}
    items: list[ChecklistItemAssessment] = []
    for item in checklist.items:
        status = str(item_statuses.get(item.key, ChecklistStatus.NOT_CHECKED))
        shown = by_key.get(item.key) if status != ChecklistStatus.NOT_CHECKED else None
        items.append(
            ChecklistItemAssessment(
                key=item.key,
                label=item.label,
                category=item.category,
                blocking=item.blocking,
                status=status,
                detail=_item_detail(status, shown.result if shown else None),
                stale=bool(stale.get(item.key)),
                result=shown.result if shown else None,
                evidence=shown.evidence if shown else (),
            )
        )
    items.extend(
        ChecklistItemAssessment(
            key=key,
            label=key,
            category=None,
            blocking=False,
            status=str(entry.result.status),
            detail=str(entry.result.detail),
            stale=False,
            result=entry.result,
            evidence=entry.evidence,
        )
        for key, entry in sorted(by_key.items())
        if checklist.item(key) is None and entry.result.status in ATTENTION_STATUSES
    )
    categories = statuses.get("categories") or {}
    groups = tuple(
        ChecklistGroupAssessment(
            key=category.key,
            label=category.label,
            status=(group := str(categories.get(category.key, ChecklistStatus.NOT_APPLICABLE))),
            detail=_detail(members := [item for item in items if item.category == category.key], group),
            item_keys=tuple(item.key for item in members),
        )
        for category in checklist.categories
    )
    summary = str(statuses.get("status") or ChecklistStatus.NOT_CHECKED)
    declared = items[: len(checklist.items)]
    return ChecklistAssessment(
        status=summary,
        detail=_detail(items, summary),
        complete=all(item.status != ChecklistStatus.NOT_CHECKED for item in declared),
        stale=any(item.stale for item in declared),
        groups=groups,
        items=tuple(items),
    )


class LatestResults(InstancePrefetch):
    """Each listed record's latest readable result per item, read once for the page.

    The latest row per item is chosen structurally (``first_per_group`` over the
    page's rows) before the reader's scope applies, so an unreadable latest
    result leaves its item without one. Each row is a :class:`LatestResult`.
    """

    single = False

    def related(
        self,
        instances: list[Any],
        queryset: models.QuerySet[Any],
    ) -> tuple[Iterable[Any], Callable[[Any], object], Callable[[Any], object]]:
        if not instances:
            return [], lambda entry: entry.result.object_id, lambda instance: instance.pk
        model = type(instances[0])
        actor = self.reader(queryset)
        rows = _result_model().objects.latest_readable(model, [instance.pk for instance in instances], actor)
        return rows, lambda entry: entry.result.object_id, lambda instance: instance.pk


class ChecklistResultQuerySet[ResultT: ChecklistResult](AppendOnlyQuerySet[ResultT], AngeeQuerySet[ResultT]):
    """Results are recorded once, through :meth:`ChecklistResultManager.record`."""

    def for_record(self, record: models.Model) -> Self:
        """Return the results on one record, keyed by its canonical generic target."""

        return self.filter(**generic_target(record).lookups(self.model, "record"))


_ChecklistResultManagerBase = AngeeManager.from_queryset(ChecklistResultQuerySet)


class ChecklistResultManager[ResultT: ChecklistResult](_ChecklistResultManagerBase[ResultT]):
    """Records results and reads them through the one status rule."""

    def record(
        self,
        record: ChecklistMixin,
        key: str,
        status: str,
        detail: str = "",
        *,
        evidence: Iterable[models.Model] = (),
    ) -> ResultT:
        """Record one result for item ``key`` on ``record`` under the ambient actor.

        The key must be declared by the record's checklist, and ``status`` must be
        an outcome of :class:`~angee.checklists.contracts.ChecklistStatus`: ``not_checked``
        is the absence of a result, never one. The record's content token is read
        here and stored with the result, so the caller holds the record's row lock
        from the reads its check made until this call; otherwise a concurrent edit
        could be stamped as checked content. Each evidence record must be of a type
        the recording addon declares on ``checklists/evidence``, and readable by the
        recording actor; actorless recording requires a named system context. The
        result's own ``create`` gate requires the record type's declared write.
        """

        model = type(record)
        if not isinstance(record, _mixin()):
            raise TypeError(f"{model._meta.label} does not compose ChecklistMixin.")
        if model.checklist().item(key) is None:
            raise ValidationError({"key": f"{model._meta.label} declares no checklist item {key!r}."})
        if status not in ChecklistStatus.values:
            raise ValidationError({"status": f"{status!r} is not a checklist status."})
        if status == ChecklistStatus.NOT_CHECKED:
            raise ValidationError({"status": "A result records an outcome; not checked is the absence of one."})
        links = list(dict.fromkeys(evidence))
        evidence_model = _evidence_model()
        for link in links:
            evidence_model.validate_target(link)
        actor = current_actor()
        if actor is not None:
            if links:
                readable_records([EvidenceReference.of(link) for link in links], [actor])
        elif not is_sudo():
            raise PermissionDenied("Recording a checklist result without an actor requires a named system context.")
        revision = model.checklist_revision_expression()
        token = (
            ""
            if revision is None
            else model._base_manager.filter(pk=record.pk)
            .annotate(
                _checklist_revision=revision,
            )
            .values_list("_checklist_revision", flat=True)
            .get()
        )
        with transaction.atomic():
            result = self.create(
                key=key,
                status=status,
                detail=detail,
                revision=token or "",
                **generic_target(record).lookups(self.model, "record"),
            )
            if links:
                with system_context(reason="checklists.record_evidence"):
                    evidence_model.objects.bulk_create(
                        [
                            evidence_model(result=result, **generic_target(link).lookups(evidence_model, "record"))
                            for link in links
                        ]
                    )
        return result

    def latest_readable(self, model: type[models.Model], record_ids: Sequence[Any], actor: Any) -> list[LatestResult]:
        """The latest readable result per item of each listed record, with its evidence, in one read.

        The latest row is selected structurally, then the reader's scope applies,
        so an item whose latest result the reader cannot read has none.
        """

        content_type = _target_content_type(model)
        latest = self.model._base_manager.filter(content_type=content_type, object_id__in=record_ids).first_per_group(
            "object_id",
            "key",
            order_by=LATEST_ORDER,
            limit=1,
        )
        links = (
            _evidence_model()
            ._base_manager.filter(result_id=models.OuterRef("pk"))
            .order_by("pk")
            .values(_item=JSONObject(content_type=models.F("content_type_id"), object_id=models.F("object_id")))
        )
        rows = (
            reader_queryset(self.model, actor)
            .filter(pk__in=latest.values("pk"))
            .annotate(_evidence=JSONArraySubquery(links))
            .order_by("pk")
        )
        return [LatestResult(result=row, evidence=_references(cast(Any, row)._evidence)) for row in rows]

    def assess(self, record: ChecklistMixin) -> ChecklistAssessment:
        """Assess one record for the ambient actor through the same rule its list reads."""

        model = type(record)
        rows = model._default_manager.filter(pk=record.pk)
        statuses = (
            rows.annotate(_statuses=ChecklistStatuses.for_rows(rows, "projection"))
            .values_list(
                "_statuses",
                flat=True,
            )
            .first()
        )
        return assessment(model.checklist(), statuses or {}, self.latest_readable(model, [record.pk], current_actor()))

    def counts(
        self,
        rows: models.QuerySet[Any],
    ) -> tuple[list[tuple[str, int]], list[tuple[str, str, str, int]]]:
        """Count records by summary status, and by category and attention status, in one statement.

        Summary buckets are the nonempty statuses, summing to the rows counted.
        Attention lists every declared category, in order with its label, and each
        attention status, zero included: ``(category, label, status, count)``.
        """

        checklist: Checklist = rows.model.checklist()
        grouped = (
            rows.order_by()
            .annotate(
                _checklist_vector=ChecklistStatuses.for_rows(rows, "vector"),
            )
            .values("_checklist_vector")
            .annotate(count=models.Count("pk"))
            .order_by()
        )
        summary: dict[str, int] = {}
        attention: dict[tuple[str, str], int] = {}
        for row in grouped:
            vector = row["_checklist_vector"]
            if not isinstance(vector, dict):
                raise ValueError("The checklist status projection has the wrong shape.")
            summary[vector["status"]] = summary.get(vector["status"], 0) + row["count"]
            for category, status in (vector.get("categories") or {}).items():
                if status in ATTENTION_STATUSES:
                    attention[category, status] = attention.get((category, status), 0) + row["count"]
        return sorted(summary.items()), [
            (category.key, category.label, status, attention.get((category.key, status), 0))
            for category in checklist.categories
            for status in ATTENTION_STATUSES
        ]


def _references(links: Iterable[Mapping[str, Any]]) -> tuple[EvidenceReference, ...]:
    """Name each evidence link's record publicly, in link order."""

    references = []
    for link in links:
        target = ContentType.objects.get_for_id(link["content_type"]).model_class()
        if target is not None:
            references.append(EvidenceReference(model=target._meta.label, id=public_id_for(target, link["object_id"])))
    return tuple(references)


def checklist_status(rows: models.QuerySet[Any]) -> models.Expression:
    """The record's summary status, for list cells and the ``checklist_status`` filter."""

    return ChecklistStatuses.for_rows(rows, "status")


def checklist_category_status(rows: models.QuerySet[Any], *, category: str) -> models.Expression:
    """One category's status, for the ``checklist_<category>`` filters."""

    if not rows.model.checklist().category_items(category):
        return models.Value(ChecklistStatus.NOT_APPLICABLE, output_field=models.CharField())
    return ChecklistStatuses.for_rows(rows, ("category", category))


def checklist_next_blocking(rows: models.QuerySet[Any]) -> models.Expression:
    """The first blocking item that has not passed, or ``NULL``: records still blocked filter on it."""

    return ChecklistStatuses.for_rows(rows, "next_blocking")


class ChecklistEvidenceQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Evidence links are authored once with their result."""


ChecklistEvidenceManager = AngeeManager.from_queryset(ChecklistEvidenceQuerySet)
