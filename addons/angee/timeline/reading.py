"""Compose native source queries into one SQL UNION ALL before paging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.functions import Cast

from angee.graphql.ids import PublicID
from angee.identity import public_id_for
from angee.refs import generic_pointer_target
from angee.timeline.kinds import Cursor, timeline_kinds


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


def _union(branches: list[models.QuerySet], *columns: str) -> models.QuerySet:
    """Compose already-authorized branches through Django's heterogeneous union.

    REBAC querysets deliberately rebind every union operand to the first
    queryset's policy. Here each kind owns a different policy (record-gated
    history/chatter alongside independently readable targets). Preserve those
    eagerly scoped operands with Django's native QuerySet, then project only
    the common columns. No actor or model policy is applied across the union.
    """
    first, *rest = [rows.values(*columns) for rows in branches]
    native = models.QuerySet(model=first.model, query=first.query.clone(), using=first.db).values(*columns)
    return native.union(*rest, all=True) if rest else native


class Timeline:
    """A record-authorized read; each kind independently authorizes its targets."""

    def __init__(self, record: models.Model, actor: Any):
        self.record = record
        self.kinds = tuple(kind for kind in timeline_kinds() if kind.eligible(type(record)))
        self.branches = {}
        self.content_types = {}
        self.folding = set()
        for kind in self.kinds:
            rows = kind.queryset(record, actor).order_by()
            if not rows.exists():
                continue
            content_type = ContentType.objects.get_for_model(rows.model)
            self.content_types[kind.id] = content_type.pk
            fold = kind.fold(record)
            if fold is not None:
                self.folding.add(kind.id)
            fold_type, fold_pk = fold or (
                models.Value(None, models.IntegerField()), models.Value(None, models.BigIntegerField()),
            )
            waiting = kind.waiting_on(rows, actor).filter(pk=models.OuterRef("pk"))
            self.branches[kind.id] = rows.annotate(
                _timeline_kind=models.Value(kind.id, output_field=models.CharField()),
                _timeline_at=kind.at,
                _timeline_pk=Cast("pk", output_field=models.BigIntegerField()),
                _timeline_needs=models.Exists(waiting),
                _timeline_fold_type=fold_type,
                _timeline_fold_pk=fold_pk,
            )

    @cached_property
    def lines(self) -> dict[str, models.QuerySet]:
        result = {}
        for kind, rows in self.branches.items():
            if kind not in self.folding:
                result[kind] = rows
                continue
            parents = models.Q(pk__in=[])
            for parent_kind, parent_rows in self.branches.items():
                targets = parent_rows.filter(pk=models.OuterRef("_timeline_fold_pk"))
                if parent_kind == kind:
                    targets = targets.exclude(pk=models.OuterRef("pk"))
                parents |= models.Q(_timeline_fold_type=self.content_types[parent_kind]) & models.Q(
                    models.Exists(targets),
                )
            result[kind] = rows.filter(~parents)
        return result

    @cached_property
    def counts(self) -> dict[str, tuple[int, int]]:
        if not self.lines:
            return {}
        branches = [rows.values("_timeline_kind").annotate(
            _timeline_count=models.Count("pk", distinct=True),
            _timeline_waiting=models.Count("pk", distinct=True, filter=models.Q(_timeline_needs=True)),
        ) for rows in self.lines.values()]
        return {row["_timeline_kind"]: (row["_timeline_count"], row["_timeline_waiting"])
                for row in _union(branches, "_timeline_kind", "_timeline_count", "_timeline_waiting")}

    def attention(self) -> int:
        return sum(waiting for _, waiting in self.counts.values())

    def page(self, *, kinds: list[str] | None = None, needs_me: bool = False,
             cursor: Cursor | None = None, limit: int = 25) -> Page:
        branches = []
        for kind, rows in self.lines.items():
            if kinds and kind not in kinds:
                continue
            if needs_me:
                rows = rows.filter(_timeline_needs=True)
            if cursor is not None:
                rows = rows.filter(cursor.predicate())
            branches.append(rows.values("_timeline_kind", "_timeline_at", "_timeline_pk", "_timeline_needs"))
        selected = list(_union(
            branches, "_timeline_kind", "_timeline_at", "_timeline_pk", "_timeline_needs",
        ).order_by("-_timeline_at", "_timeline_kind", "-_timeline_pk")[:limit + 1]) if branches else []
        more = len(selected) > limit
        selected = selected[:limit]
        ids: dict[str, set[int]] = {}
        for row in selected:
            ids.setdefault(row["_timeline_kind"], set()).add(row["_timeline_pk"])
        hydrated = {kind: self.branches[kind].filter(pk__in=pks).in_bulk() for kind, pks in ids.items()}
        children = self._children(ids)
        record_type, record_pk = generic_pointer_target(self.record)
        owners = {kind.id: kind for kind in self.kinds}
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
            entries=entries, kinds=[KindSummary(kind.id, self.counts.get(kind.id, (0, 0))[0]) for kind in self.kinds],
            needs_me=self.attention(), has_more=more,
            next_before=Cursor(
                last["_timeline_at"], last["_timeline_kind"], last["_timeline_pk"],
            ).encode() if last else None,
        )

    def _children(self, ids: dict[str, set[int]]) -> dict[tuple[int, int], list[KindCount]]:
        if not ids or not self.folding:
            return {}
        scope = models.Q(pk__in=[])
        for kind, pks in ids.items():
            scope |= models.Q(_timeline_fold_type=self.content_types[kind], _timeline_fold_pk__in=pks)
        # Selected parents are readable top-level lines already. Only a line's
        # self-reference needs excluding; rechecking every parent repeats the page query.
        branches = [rows.filter(scope).exclude(pk__in=ids.get(kind, ()))
                    .values("_timeline_fold_type", "_timeline_fold_pk", "_timeline_kind")
                    .annotate(_timeline_count=models.Count("pk", distinct=True))
                    for kind, rows in self.branches.items() if kind in self.folding]
        result: dict[tuple[int, int], list[KindCount]] = {}
        for row in _union(
            branches, "_timeline_fold_type", "_timeline_fold_pk", "_timeline_kind", "_timeline_count",
        ).order_by("_timeline_kind"):
            key = row["_timeline_fold_type"], row["_timeline_fold_pk"]
            result.setdefault(key, []).append(KindCount(row["_timeline_kind"], row["_timeline_count"]))
        return result
