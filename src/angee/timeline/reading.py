"""Read a timeline page, its kind totals and folded children in one statement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.functions import Cast
from django_cte import CTE, with_cte
from rebac import to_subject_ref

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


_SOURCE = (
    "_timeline_kind", "_timeline_type", "_timeline_at", "_timeline_pk",
    "_timeline_needs", "_timeline_fold_type", "_timeline_fold_pk",
)
# One shape for the statement's three row sets: a page entry, a kind total, a folded child group.
_RESULT: dict[str, models.Field[Any, Any]] = {
    "row": models.CharField(), "kind": models.CharField(), "at": models.DateTimeField(),
    "pk": models.BigIntegerField(), "needs": models.BooleanField(), "type": models.IntegerField(),
    "count": models.IntegerField(), "waiting": models.IntegerField(),
}
# Kinds name their columns `_timeline_<column>`; the statement's result names them `_result_<column>`.
_ORDER = KeysetOrder("at", tie_breakers=("kind",), key_field="pk")


def _native(rows: models.QuerySet, *columns: str) -> models.QuerySet:
    """Project a relation through Django's native QuerySet, applying no model's policy.

    REBAC querysets rebind every union operand to the first operand's policy, and
    a CTE's reader comes from its first model's default manager. Each kind's branch
    already carries its own eager read scope, so the composed relations add none.
    """
    return models.QuerySet[models.Model](
        model=rows.model, query=rows.query.clone(), using=rows.db,
    ).values(*columns)


def _union(branches: list[models.QuerySet], *columns: str) -> models.QuerySet:
    """Combine independently authorized projections with Django's heterogeneous union."""
    first, *rest = [rows.values(*columns) for rows in branches]
    return _native(first, *columns).union(*rest, all=True).order_by()


def _rows(cte: CTE) -> models.QuerySet:
    return _native(cte.queryset(), *_SOURCE).order_by()


def _part(rows: models.QuerySet, row: str, **columns: Any) -> models.QuerySet:
    """Project one row set onto the result shape; absent columns are typed NULLs."""
    columns["row"] = models.Value(row, output_field=models.CharField())
    return rows.values(**{
        f"_result_{name}": columns.get(name, Cast(models.Value(None), output_field=field))
        for name, field in _RESULT.items()
    })


def _totals(lines: CTE) -> models.QuerySet:
    totals = _rows(lines).values("_timeline_kind").annotate(
        _timeline_count=models.Count("_timeline_pk", distinct=True),
        _timeline_waiting=models.Count("_timeline_pk", distinct=True, filter=models.Q(_timeline_needs=True)),
    )
    return _part(
        totals, "kind", kind=models.F("_timeline_kind"),
        count=models.F("_timeline_count"), waiting=models.F("_timeline_waiting"),
    )


class Timeline:
    """A record-authorized read; each kind independently authorizes its targets.

    The readable kinds' rows form one statement-local source relation, so a page,
    every kind's totals and the page's folded children are read by one statement.
    """

    def __init__(self, record: models.Model, actor: Any):
        self.record = record
        self.actor = to_subject_ref(actor) if actor is not None else None
        self.kinds = tuple(kind for kind in timeline_kinds() if kind.eligible(type(record)))
        self.sources: dict[str, models.QuerySet] = {}
        branches = []
        for kind in self.kinds:
            rows = kind.queryset(record, actor).order_by()
            # An empty kind stays out of the statement: compiling its read scope and
            # attention subquery there costs more than this probe of its bare scope.
            if not rows.exists():
                continue
            self.sources[kind.id] = rows
            fold_type, fold_pk = kind.fold(record) or (models.Value(None), models.Value(None))
            waiting = kind.waiting_on(rows, actor).filter(pk=models.OuterRef("pk"))
            branches.append(rows.annotate(
                _timeline_kind=models.Value(kind.id, output_field=models.CharField()),
                _timeline_type=models.Value(
                    ContentType.objects.get_for_model(rows.model).pk, output_field=models.IntegerField(),
                ),
                _timeline_at=kind.at,
                _timeline_pk=Cast("pk", output_field=models.BigIntegerField()),
                _timeline_needs=models.Exists(waiting),
                # Typed, so a leading NULL operand cannot resolve the union column as text.
                _timeline_fold_type=Cast(fold_type, output_field=models.IntegerField()),
                _timeline_fold_pk=Cast(fold_pk, output_field=models.BigIntegerField()),
            ))
        self.source = CTE(_union(branches, *_SOURCE), name="timeline_source") if branches else None

    @cached_property
    def lines(self) -> CTE | None:
        """Source rows not folded under a readable parent target occurring on this record."""
        if self.source is None:
            return None
        source = _rows(self.source)
        parents = source.filter(
            _timeline_type=models.OuterRef("_timeline_fold_type"), _timeline_pk=models.OuterRef("_timeline_fold_pk"),
        ).exclude(_timeline_kind=models.OuterRef("_timeline_kind"), _timeline_pk=models.OuterRef("_timeline_pk"))
        return CTE(source.filter(~models.Exists(parents)), name="timeline_lines")

    @cached_property
    def counts(self) -> dict[str, tuple[int, int]]:
        if self.lines is None:
            return {}
        return {row["_result_kind"]: (row["_result_count"], row["_result_waiting"])
                for row in with_cte(self.source, self.lines, select=_totals(self.lines))}

    def attention(self) -> int:
        return sum(waiting for _, waiting in self.counts.values())

    def _read(self, *, kinds: list[str] | None, needs_me: bool, anchor: tuple[Any, ...] | None,
              limit: int) -> tuple[list[dict[str, Any]], dict[tuple[int, int], list[KindCount]]]:
        """Read the page with its look-ahead row, every kind's totals and the page's children together."""
        assert self.source is not None and self.lines is not None
        rows = _rows(self.lines)
        if kinds:
            rows = rows.filter(_timeline_kind__in=kinds)
        if needs_me:
            rows = rows.filter(_timeline_needs=True)
        if anchor is not None:
            rows = rows.filter(_ORDER.before(anchor, prefix="_timeline_"))
        page = CTE(rows.order_by(*_ORDER.ordering(prefix="_timeline_"))[:limit + 1], name="timeline_page")
        entries = _part(
            _rows(page), "entry", kind=models.F("_timeline_kind"), at=models.F("_timeline_at"),
            pk=models.F("_timeline_pk"), needs=models.F("_timeline_needs"), type=models.F("_timeline_type"),
        )
        # A page line's children are the folding rows targeting it; selected lines
        # are top-level already, so only a line's self-reference is excluded.
        children = _rows(self.source).filter(models.Exists(_rows(page).filter(
            _timeline_type=models.OuterRef("_timeline_fold_type"), _timeline_pk=models.OuterRef("_timeline_fold_pk"),
        ))).exclude(models.Exists(_rows(page).filter(
            _timeline_kind=models.OuterRef("_timeline_kind"), _timeline_pk=models.OuterRef("_timeline_pk"),
        ))).values("_timeline_fold_type", "_timeline_fold_pk", "_timeline_kind").annotate(
            _timeline_count=models.Count("_timeline_pk", distinct=True),
        )
        statement = with_cte(self.source, self.lines, page, select=entries.union(
            _totals(self.lines),
            _part(children, "child", kind=models.F("_timeline_kind"), pk=models.F("_timeline_fold_pk"),
                  type=models.F("_timeline_fold_type"), count=models.F("_timeline_count")),
            all=True,
        ).order_by(*_ORDER.ordering(prefix="_result_")))
        selected: list[dict[str, Any]] = []
        counts: dict[str, tuple[int, int]] = {}
        children_of: dict[tuple[int, int], list[KindCount]] = {}
        for row in statement:
            if row["_result_row"] == "entry":
                selected.append(row)
            elif row["_result_row"] == "kind":
                counts[row["_result_kind"]] = row["_result_count"], row["_result_waiting"]
            else:
                children_of.setdefault((row["_result_type"], row["_result_pk"]), []).append(
                    KindCount(row["_result_kind"], row["_result_count"]),
                )
        self.__dict__["counts"] = counts
        return selected, children_of

    def page(self, *, kinds: list[str] | None = None, needs_me: bool = False,
             before: str | None = None, limit: int = 25) -> Page:
        selected_kinds = sorted(set(kinds)) if kinds else sorted(kind.id for kind in self.kinds)
        signer = keyset_signer(
            [self.record._state.db, self.record._meta.label_lower, str(self.record.pk),
             str(self.actor), selected_kinds, needs_me],
            salt="angee.timeline",
        )
        # Every union branch explicitly projects its native key as bigint.
        anchor = _ORDER.unsign(before, signer, models.BigIntegerField()) if before is not None else None
        selected, children = self._read(
            kinds=kinds, needs_me=needs_me, anchor=anchor, limit=limit,
        ) if self.source is not None else ([], {})
        more = len(selected) > limit
        selected = selected[:limit]
        ids: dict[str, set[int]] = {}
        for row in selected:
            ids.setdefault(row["_result_kind"], set()).add(row["_result_pk"])
        hydrated = {kind: self.sources[kind].filter(pk__in=pks).in_bulk() for kind, pks in ids.items()}
        record_type, record_pk = generic_pointer_target(self.record)
        owners = {kind.id: kind for kind in self.kinds}
        displays = {kind: owners[kind].present(rows.values()) for kind, rows in hydrated.items()}
        entries = []
        for row in selected:
            kind_id, pk = row["_result_kind"], row["_result_pk"]
            target = hydrated[kind_id].get(pk)
            if target is None:  # A source can be deleted between selection and hydration.
                continue
            owner = owners[kind_id]
            display = displays[kind_id][pk]
            label = target._meta.label
            entries.append(Entry(
                key=f"{kind_id}:{label}:{pk}:{record_type.pk}:{record_pk}:{owner.occurrence}",
                at=row["_result_at"], kind=kind_id, title=display.title, actor=display.actor, detail=display.detail,
                needs_me=row["_result_needs"], target_model=label,
                target_id=PublicID(public_id_for(type(target), pk)),
                children=children.get((row["_result_type"], pk), []),
            ))
        last = selected[-1] if selected and more else None
        return Page(
            entries=entries, kinds=[KindSummary(kind.id, self.counts.get(kind.id, (0, 0))[0]) for kind in self.kinds],
            needs_me=self.attention(), has_more=more,
            next_before=_ORDER.sign_position(
                (last["_result_at"], last["_result_kind"], last["_result_pk"]), signer,
            ) if last else None,
        )
