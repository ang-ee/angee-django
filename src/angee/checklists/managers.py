"""Recording checklist results and the one SQL rule that assesses them.

:class:`ChecklistStatuses` is the status rule. For each declared item it reads
the latest structural result row; the row counts only when the reader may read
it, so an unreadable latest result reads ``not_checked`` and never falls back to
an older one. A passed or not-applicable result recorded against another content
token reads ``unresolved``. Categories and the record combine their items, and a
result whose key left the checklist still counts while it needs attention. The
record's token is computed once per row in a derived input, which PostgreSQL
keeps unflattened (``OFFSET 0``) so each item is assessed once.

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
from django.db.models.functions import Coalesce, JSONObject
from django.db.models.lookups import Exact, In
from rebac import current_actor, generic_target, system_context
from rebac.actors import is_sudo
from rebac.field_backing import canonical_model
from rebac.relation_loading import relation_actor

from angee.base.expressions import JSONArraySubquery
from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyQuerySet
from angee.base.models.identity import public_id_for
from angee.base.permissions import reader_queryset
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


def _readable_results(actor: Any) -> models.QuerySet[Any]:
    """The results ``actor`` may read, as a subquery-ready aggregate scope; ambient without an actor."""

    return cast(Any, reader_queryset(_result_model(), actor)).scoped_for_aggregate()


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
        self.readable = _readable_results(actor)
        target = canonical_model(model) or model
        self.target = (target._meta.app_label, target._meta.model_name)
        if isinstance(select, tuple):
            self.items = self.checklist.category_items(select[1])
        else:
            self.items = self.checklist.items
        self.retired = select in ("status", "vector", "projection")
        self.stale = select == "projection"
        revision = model.checklist_revision_expression()
        if revision is None:
            revision = models.Value("", output_field=models.CharField())
        sources: list[Any] = [revision, *(self._item_status(item.key) for item in self.items)]
        if self.retired:
            sources.append(self._retired_status())
        if self.stale:
            sources.extend(self._item_stale(item.key) for item in self.items)
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

    def _shown(self) -> models.Exists:
        return models.Exists(self.readable.filter(pk=models.OuterRef("pk")))

    def _fresh(self) -> models.Q:
        return models.Q(revision=_InputColumn(_INPUT, _REVISION, models.CharField()))

    def _item_status(self, key: str) -> models.Expression:
        status = models.Case(
            models.When(~models.Q(self._shown()), then=models.Value(ChecklistStatus.NOT_CHECKED)),
            models.When(
                models.Q(status__in=PASSING_STATUSES) & ~self._fresh(),
                then=models.Value(ChecklistStatus.UNRESOLVED),
            ),
            default=models.F("status"),
            output_field=models.CharField(),
        )
        latest = self._results().filter(key=key).annotate(_status=status).order_by(*LATEST_ORDER)
        return Coalesce(
            models.Subquery(latest.values("_status")[:1]),
            models.Value(ChecklistStatus.NOT_CHECKED),
            output_field=models.CharField(),
        )

    def _item_stale(self, key: str) -> models.Expression:
        stale = models.Case(
            models.When(models.Q(self._shown()) & ~self._fresh(), then=models.Value(True)),
            default=models.Value(False),
            output_field=models.BooleanField(),
        )
        latest = self._results().filter(key=key).annotate(_stale=stale).order_by(*LATEST_ORDER)
        return Coalesce(
            models.Subquery(latest.values("_stale")[:1]),
            models.Value(False),
            output_field=models.BooleanField(),
        )

    def _retired_status(self) -> models.Subquery:
        """The strongest attention status among the readable latest results of retired keys."""

        latest = (
            self._results("object_id")
            .exclude(key__in=self.checklist.keys)
            .first_per_group(
                "key",
                order_by=LATEST_ORDER,
                limit=1,
            )
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

    def _columns(self) -> tuple[list[_InputColumn], _InputColumn | None, list[_InputColumn]]:
        items = [_InputColumn(_ITEMS, f"i{index}", models.CharField()) for index in range(len(self.items))]
        retired = _InputColumn(_ITEMS, "r", models.CharField(null=True)) if self.retired else None
        stale = (
            [_InputColumn(_ITEMS, f"s{index}", models.BooleanField()) for index in range(len(self.items))]
            if self.stale
            else []
        )
        return items, retired, stale

    def _summary(self, items: Sequence[_InputColumn], retired: _InputColumn | None) -> models.Expression:
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
    def _category(items: Sequence[_InputColumn]) -> models.Expression:
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

    def _categories(self, items: Sequence[_InputColumn]) -> dict[str, models.Expression]:
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
        revision, *sources = self.get_source_expressions()
        revision_sql, revision_params = compiler.compile(revision)
        names = [f"i{index}" for index in range(len(self.items))]
        if self.retired:
            names.append("r")
        if self.stale:
            names.extend(f"s{index}" for index in range(len(self.items)))
        compiled = [compiler.compile(source) for source in sources]
        output_sql, output_params = compiler.compile(self._output().resolve_expression(compiler.query))
        # PostgreSQL otherwise flattens each derived input into its readers and
        # repeats the record's token and every item read per reference.
        keep = " OFFSET 0" if connection.vendor == "postgresql" else ""
        columns = ", ".join(f"{sql} AS {quote(name)}" for (sql, _params), name in zip(compiled, names, strict=True))
        sql = (
            f"(SELECT {output_sql} FROM (SELECT {columns or 'NULL AS ' + quote('_')} FROM "
            f"(SELECT {revision_sql} AS {quote(_REVISION)}{keep}) AS {quote(_INPUT)}{keep}) AS {quote(_ITEMS)})"
        )
        params = (*output_params, *(param for _sql, params in compiled for param in params), *revision_params)
        return sql, params


@dataclass(frozen=True)
class ChecklistItemAssessment:
    """One item's assessed status with its latest readable result, if any."""

    key: str
    label: str
    category: str | None
    blocking: bool
    status: str
    detail: str
    stale: bool
    result: Any = None

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


def _item_detail(status: str, result: Any) -> str:
    if result is None:
        return NO_RESULT_DETAIL
    if status != result.status and result.status in PASSING_STATUSES:
        return f"The record changed after this check. Earlier result: {result.detail}"
    return str(result.detail)


def assessment(checklist: Checklist, statuses: Mapping[str, Any], results: Iterable[Any]) -> ChecklistAssessment:
    """Build a record's assessment from its SQL statuses and its latest readable results."""

    by_key = {result.key: result for result in results}
    item_statuses = statuses.get("items") or {}
    stale = statuses.get("stale") or {}
    items = [
        ChecklistItemAssessment(
            key=item.key,
            label=item.label,
            category=item.category,
            blocking=item.blocking,
            status=(status := item_statuses.get(item.key, ChecklistStatus.NOT_CHECKED)),
            detail=_item_detail(status, by_key.get(item.key) if status != ChecklistStatus.NOT_CHECKED else None),
            stale=bool(stale.get(item.key)),
            result=by_key.get(item.key) if status != ChecklistStatus.NOT_CHECKED else None,
        )
        for item in checklist.items
    ]
    items.extend(
        ChecklistItemAssessment(
            key=result.key,
            label=result.key,
            category=None,
            blocking=False,
            status=str(result.status),
            detail=str(result.detail),
            stale=False,
            result=result,
        )
        for key, result in sorted(by_key.items())
        if checklist.item(key) is None and result.status in ATTENTION_STATUSES
    )
    categories = statuses.get("categories") or {}
    groups = tuple(
        ChecklistGroupAssessment(
            key=category.key,
            label=category.label,
            status=(group := categories.get(category.key, ChecklistStatus.NOT_APPLICABLE)),
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
    result leaves its item without one. Each result carries its readable
    evidence references.
    """

    single = False

    def related(
        self,
        instances: list[Any],
        queryset: models.QuerySet[Any],
    ) -> tuple[Iterable[Any], Callable[[Any], object], Callable[[Any], object]]:
        if not instances:
            return [], lambda row: row.object_id, lambda instance: instance.pk
        model = type(instances[0])
        actor = self.reader(queryset)
        rows = _result_model().objects.latest_readable(model, [instance.pk for instance in instances], actor)
        return rows, lambda row: row.object_id, lambda instance: instance.pk


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

        The key must be declared by the record's checklist and ``status`` belong to
        :class:`~angee.checklists.models.ChecklistStatus`. The record's current content
        token is stored with it. Each evidence record must be of a type the
        recording addon declares on ``checklists/evidence``, and readable by the
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

    def latest_readable(self, model: type[models.Model], record_ids: Sequence[Any], actor: Any) -> list[ResultT]:
        """The latest readable result per item of each listed record, with its evidence, in one read.

        The latest row is selected structurally, then the reader's scope applies,
        so an item whose latest result the reader cannot read has none. A shown
        result's evidence is readable by construction (``checklists/result#read``),
        so each row carries its evidence references as ``evidence_references``.
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
        rows = list(
            reader_queryset(self.model, actor)
            .filter(pk__in=latest.values("pk"))
            .annotate(_evidence=JSONArraySubquery(links))
            .order_by("pk")
        )
        for row in rows:
            references = []
            for link in cast(Any, row)._evidence:
                target = ContentType.objects.get_for_id(link["content_type"]).model_class()
                if target is not None:
                    references.append(
                        EvidenceReference(model=target._meta.label, id=public_id_for(target, link["object_id"])),
                    )
            cast(Any, row).evidence_references = tuple(references)
        return cast(list[ResultT], rows)

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
        results = self.latest_readable(model, [record.pk], current_actor())
        return assessment(model.checklist(), statuses or {}, results)

    def counts(self, rows: models.QuerySet[Any]) -> tuple[list[tuple[str, int]], list[tuple[str, str, int]]]:
        """Count records by summary status, and by category and attention status, in one statement.

        Summary buckets are the nonempty statuses, summing to the rows counted;
        attention lists every category and attention status, zero included.
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
            (category.key, status, attention.get((category.key, status), 0))
            for category in checklist.categories
            for status in ATTENTION_STATUSES
        ]


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
