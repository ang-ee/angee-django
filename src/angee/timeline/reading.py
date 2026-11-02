"""Read one authorized source relation for folding, counts and paging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import connections, models
from django.db.models.functions import Cast
from django_cte import CTE, with_cte
from rebac import database_context, to_subject_ref

from angee.base.models.identity import public_id_for
from angee.base.pagination import KeysetOrder, keyset_signer
from angee.base.refs import generic_pointer_target
from angee.graphql.ids import PublicID
from angee.timeline.kinds import timeline_kinds


@dataclass
class KindCount:
    kind: str
    count: int


@dataclass
class KindSummary:
    id: str
    count: int


@dataclass
class Entry:
    key: str
    at: datetime
    kind: str
    title: str
    actor: str
    detail: str
    needs_me: bool
    target_model: str
    target_id: PublicID
    children: list[KindCount]


@dataclass
class Page:
    entries: list[Entry]
    kinds: list[KindSummary]
    needs_me: int
    has_more: bool
    next_before: str | None


_SOURCE_COLUMNS = (
    "_timeline_kind", "_timeline_type", "_timeline_at", "_timeline_pk",
    "_timeline_needs", "_timeline_fold_type", "_timeline_fold_pk",
)
_PAGE_COLUMNS = ("_timeline_kind", "_timeline_type", "_timeline_at", "_timeline_pk", "_timeline_needs")
_RESULT_COLUMNS = (
    "_timeline_row", "_timeline_result_kind", "_timeline_result_at", "_timeline_result_pk",
    "_timeline_result_needs", "_timeline_result_type", "_timeline_result_count", "_timeline_result_waiting",
)


def _native(rows: models.QuerySet, *columns: str) -> models.QuerySet:
    """Project a heterogeneous relation without inventing one model's policy.

    Each source operand already owns its eager read scope. REBAC queryset
    combinations rebind operands to the first model's policy, and a CTE's
    default manager belongs to that same arbitrary model. Native Django owns
    both synthetic relations; source predicates and their witnesses stay inside.
    """
    return models.QuerySet[models.Model](
        model=rows.model, query=rows.query.clone(), using=rows.db,
    ).values(*columns)


def _union(branches: list[models.QuerySet], *columns: str) -> models.QuerySet:
    """Combine independently authorized source projections in native Django."""
    first, *rest = [rows.values(*columns) for rows in branches]
    return _native(first, *columns).union(*rest, all=True).order_by()


class Timeline:
    """A record-authorized read; each kind independently authorizes its targets."""

    def __init__(self, record: models.Model, actor: Any):
        self.record = record
        self.actor = actor
        self.kinds = tuple(kind for kind in timeline_kinds() if kind.eligible(type(record)))
        self.branches = {}
        self.content_types = {}
        self.folding = set()
        for kind in self.kinds:
            rows = kind.queryset(record, actor).order_by()
            content_type = ContentType.objects.get_for_model(rows.model)
            self.content_types[kind.id] = content_type.pk
            fold = kind.fold(record)
            if fold is not None:
                self.folding.add(kind.id)
            fold_type, fold_pk = fold or (models.Value(None), models.Value(None))
            self.branches[kind.id] = rows.annotate(
                _timeline_kind=models.Value(kind.id, output_field=models.CharField()),
                _timeline_type=models.Value(content_type.pk, output_field=models.IntegerField()),
                _timeline_at=kind.at,
                _timeline_pk=Cast("pk", output_field=models.BigIntegerField()),
                _timeline_needs=kind.waiting_on_expression(rows, actor),
                # Leading NULL-only UNION operands otherwise resolve as text on PostgreSQL.
                _timeline_fold_type=Cast(fold_type, output_field=models.IntegerField()),
                _timeline_fold_pk=Cast(fold_pk, output_field=models.BigIntegerField()),
            )

    @cached_property
    def source(self) -> CTE | None:
        """One statement-local source population, retaining every kind's policy."""
        if not self.branches:
            return None
        using = next(iter(self.branches.values())).db
        connection = connections[using]
        materialized = connection.vendor == "postgresql" or (
            connection.vendor == "sqlite" and connection.Database.sqlite_version_info >= (3, 35, 0)
        )
        return CTE(
            _union(list(self.branches.values()), *_SOURCE_COLUMNS),
            name="timeline_source", materialized=True if materialized else None,
        )

    def _source_rows(self) -> models.QuerySet:
        assert self.source is not None
        return _native(
            self.source.queryset().using(next(iter(self.branches.values())).db), *_SOURCE_COLUMNS,
        ).order_by()

    @cached_property
    def lines(self) -> CTE | None:
        if self.source is None:
            return None
        if not self.folding:
            return CTE(self._source_rows(), name="timeline_lines", materialized=self.source.materialized)
        parents = self._source_rows().filter(
            _timeline_type=models.OuterRef("_timeline_fold_type"),
            _timeline_pk=models.OuterRef("_timeline_fold_pk"),
        ).exclude(
            _timeline_kind=models.OuterRef("_timeline_kind"),
            _timeline_pk=models.OuterRef("_timeline_pk"),
        )
        return CTE(
            self._source_rows().filter(~models.Exists(parents)),
            name="timeline_lines", materialized=self.source.materialized,
        )

    def _line_rows(self) -> models.QuerySet:
        assert self.lines is not None
        return _native(self.lines.queryset().using(self._source_rows().db), *_SOURCE_COLUMNS).order_by()

    @cached_property
    def counts(self) -> dict[str, tuple[int, int]]:
        # An attention-only read uses the same tagged statement without hydration.
        return self._selection(
            kinds=None, needs_me=False, anchor=None, limit=1,
            order=KeysetOrder("_timeline_at", tie_breakers=("_timeline_kind",), key_field="_timeline_pk"),
        )[1]

    def _selection(self, *, kinds: list[str] | None, needs_me: bool,
                   anchor: tuple | None, limit: int, order: KeysetOrder) -> tuple[list[dict], dict, dict]:
        """Read page rows, whole-scope totals and selected-parent groups together."""
        if self.lines is None:
            return [], {}, {}

        rows = self._line_rows()
        if kinds:
            rows = rows.filter(_timeline_kind__in=kinds)
        if needs_me:
            rows = rows.filter(_timeline_needs=True)
        if anchor is not None:
            rows = rows.filter(order.before(anchor))
        page = CTE(
            rows.values(*_PAGE_COLUMNS).order_by("-_timeline_at", "_timeline_kind", "-_timeline_pk")[:limit + 1],
            name="timeline_page",
        )
        # The look-ahead entry establishes has_more, but is not a selected parent.
        selected_page = CTE(
            _native(page.queryset(), *_PAGE_COLUMNS).order_by(
                "-_timeline_at", "_timeline_kind", "-_timeline_pk",
            )[:limit],
            name="timeline_selected",
        )

        totals = self._line_rows().values("_timeline_kind").annotate(
            _timeline_count=models.Count("_timeline_pk", distinct=True),
            _timeline_waiting=models.Count("_timeline_pk", distinct=True, filter=models.Q(_timeline_needs=True)),
        ).values(
            _timeline_row=models.Value("kind", output_field=models.CharField()),
            _timeline_result_kind=models.F("_timeline_kind"),
            _timeline_result_at=Cast(models.Value(None), output_field=models.DateTimeField()),
            _timeline_result_pk=Cast(models.Value(None), output_field=models.BigIntegerField()),
            _timeline_result_needs=Cast(models.Value(None), output_field=models.BooleanField()),
            _timeline_result_type=Cast(models.Value(None), output_field=models.IntegerField()),
            _timeline_result_count=models.F("_timeline_count"),
            _timeline_result_waiting=models.F("_timeline_waiting"),
        )
        entries = _native(page.queryset(), *_PAGE_COLUMNS).order_by().values(
            _timeline_row=models.Value("entry", output_field=models.CharField()),
            _timeline_result_kind=models.F("_timeline_kind"),
            _timeline_result_at=models.F("_timeline_at"),
            _timeline_result_pk=models.F("_timeline_pk"),
            _timeline_result_needs=models.F("_timeline_needs"),
            _timeline_result_type=Cast(models.Value(None), output_field=models.IntegerField()),
            _timeline_result_count=Cast(models.Value(None), output_field=models.IntegerField()),
            _timeline_result_waiting=Cast(models.Value(None), output_field=models.IntegerField()),
        )

        selected_occurrences = _native(selected_page.queryset(), "_timeline_kind", "_timeline_pk").filter(
            _timeline_kind=models.OuterRef("_timeline_kind"),
            _timeline_pk=models.OuterRef("_timeline_pk"),
        )
        folded = selected_page.join(
            self._source_rows().filter(~models.Exists(selected_occurrences)),
            _timeline_fold_type=selected_page.col._timeline_type,
            _timeline_fold_pk=selected_page.col._timeline_pk,
        ).annotate(
            _timeline_parent_type=selected_page.col._timeline_type,
            _timeline_parent_pk=selected_page.col._timeline_pk,
        )
        children = folded.values(
            "_timeline_parent_type", "_timeline_parent_pk", "_timeline_kind",
        ).annotate(_timeline_count=models.Count("_timeline_pk", distinct=True)).values(
            _timeline_row=models.Value("child", output_field=models.CharField()),
            _timeline_result_kind=models.F("_timeline_kind"),
            _timeline_result_at=Cast(models.Value(None), output_field=models.DateTimeField()),
            _timeline_result_pk=models.F("_timeline_parent_pk"),
            _timeline_result_needs=Cast(models.Value(None), output_field=models.BooleanField()),
            _timeline_result_type=models.F("_timeline_parent_type"),
            _timeline_result_count=models.F("_timeline_count"),
            _timeline_result_waiting=Cast(models.Value(None), output_field=models.IntegerField()),
        )

        result = with_cte(
            self.source, self.lines, page, selected_page,
            select=_union([totals, entries, children], *_RESULT_COLUMNS).order_by(
                "-_timeline_result_at", "_timeline_result_kind", "-_timeline_result_pk",
            ),
        )
        selected: list[dict] = []
        counts: dict[str, tuple[int, int]] = {}
        child_counts: dict[tuple[int, int], list[KindCount]] = {}
        for row in result:
            kind = row["_timeline_result_kind"]
            if row["_timeline_row"] == "kind":
                counts[kind] = row["_timeline_result_count"], row["_timeline_result_waiting"]
            elif row["_timeline_row"] == "entry":
                selected.append({
                    "_timeline_kind": kind, "_timeline_at": row["_timeline_result_at"],
                    "_timeline_pk": row["_timeline_result_pk"],
                    "_timeline_needs": row["_timeline_result_needs"],
                })
            else:
                key = row["_timeline_result_type"], row["_timeline_result_pk"]
                child_counts.setdefault(key, []).append(KindCount(kind, row["_timeline_result_count"]))
        return selected, counts, child_counts

    def attention(self) -> int:
        return sum(waiting for _, waiting in self.counts.values())

    def page(self, *, kinds: list[str] | None = None, needs_me: bool = False,
             before: str | None = None, limit: int = 25) -> Page:
        limit = max(1, limit)
        order = KeysetOrder("_timeline_at", tie_breakers=("_timeline_kind",), key_field="_timeline_pk")
        selected_kinds = sorted(set(kinds)) if kinds else sorted(kind.id for kind in self.kinds)
        signer = keyset_signer(
            [self.record._state.db, self.record._meta.label_lower, str(self.record.pk),
             str(to_subject_ref(self.actor) if self.actor is not None else None), selected_kinds, needs_me],
            salt="angee.timeline",
        )
        # Every union branch explicitly projects its native key as bigint.
        anchor = order.unsign(before, signer, models.BigIntegerField()) if before is not None else None
        selected, counts, children = self._selection(
            kinds=kinds, needs_me=needs_me, anchor=anchor, limit=limit, order=order,
        )
        self.__dict__["counts"] = counts
        more = len(selected) > limit
        selected = selected[:limit]
        ids: dict[str, set[int]] = {}
        for row in selected:
            ids.setdefault(row["_timeline_kind"], set()).add(row["_timeline_pk"])
        owners = {kind.id: kind for kind in self.kinds}
        hydrated: dict[str, dict[int, models.Model]] = {}
        for kind, pks in ids.items():
            using = self.branches[kind].db
            with database_context(using):
                hydrated[kind] = (owners[kind].queryset(self.record, self.actor)
                    .using(using).order_by().filter(pk__in=pks).in_bulk())
        record_type, record_pk = generic_pointer_target(self.record)
        displays = {kind: owners[kind].present(rows.values()) for kind, rows in hydrated.items()}
        entries = []
        for row in selected:
            kind_id, pk = row["_timeline_kind"], row["_timeline_pk"]
            target = hydrated[kind_id].get(pk)
            if target is None:  # A source can be deleted between selection and hydration.
                continue
            owner = owners[kind_id]
            display = displays[kind_id][pk]
            label = target._meta.label
            entries.append(Entry(
                key=f"{kind_id}:{label}:{pk}:{record_type.pk}:{record_pk}:{owner.occurrence}",
                at=row["_timeline_at"], kind=kind_id, title=display.title, actor=display.actor, detail=display.detail,
                needs_me=row["_timeline_needs"], target_model=label,
                target_id=PublicID(public_id_for(type(target), pk)),
                children=children.get((self.content_types[kind_id], pk), []),
            ))
        last = selected[-1] if selected and more else None
        return Page(
            entries=entries, kinds=[KindSummary(kind.id, counts.get(kind.id, (0, 0))[0]) for kind in self.kinds],
            needs_me=self.attention(), has_more=more,
            next_before=order.sign_position(
                (last["_timeline_at"], last["_timeline_kind"], last["_timeline_pk"]), signer,
            ) if last else None,
        )
