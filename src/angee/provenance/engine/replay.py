"""Owner hooks for atomic restoration of native historical snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from graphlib import CycleError, TopologicalSorter
from typing import Any, ClassVar, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.deletion import Collector
from django.db.models.fields.reverse_related import ManyToOneRel
from simple_history.utils import get_history_manager_for_model

from .exceptions import ReplayRefused
from .policy import get_policy


class HistoryScopeMixin(models.Model):
    """Native record/ancestor/owned-relation history identity without replay policy."""

    history_relations: ClassVar[tuple[str, ...]] = ()

    class Meta:
        abstract = True

    def history_tables(self) -> tuple[type[models.Model], ...]:
        lineage = (*reversed(self._meta.get_parent_list()), type(self))
        return tuple(model for model in lineage if hasattr(model._meta, "simple_history_manager_attribute"))

    def history_identity_columns(self) -> dict[type[models.Model], tuple[str, ...]]:
        lineage = (type(self), *self._meta.get_parent_list())
        result: dict[type[models.Model], tuple[str, ...]] = {}
        for model in self.history_tables():
            owner = model
            if model not in lineage:
                owner = next(
                    (ancestor for ancestor in lineage if model._meta.get_ancestor_link(ancestor)),
                    model,
                )
            result[model] = (owner._meta.pk.attname,)
        for name in self.history_relations:
            relation = self._meta.get_field(name)
            if not isinstance(relation, ManyToOneRel) or relation.field.remote_field.parent_link:
                raise ImproperlyConfigured(f"{name}: document ownership requires a reverse foreign key.")
            result[relation.related_model] = (
                *result.get(relation.related_model, ()),
                relation.field.attname,
            )
        return result

    def _history_identity_field(self, model: type[models.Model], column: str) -> models.Field[Any, Any]:
        """Resolve the document-side identity through actual Django field metadata."""
        lineage = (*self._meta.get_parent_list(), type(self))
        field = model._meta.get_field(column)
        if isinstance(field, models.Field) and field.primary_key and field.model in lineage:
            return field
        if isinstance(field, models.ForeignKey) and field.related_model in lineage:
            return field.target_field
        return self._meta.pk

    def history_scopes(self) -> dict[type[models.Model], models.Q]:
        scopes = {}
        for model, columns in self.history_identity_columns().items():
            predicate: models.Q | None = None
            for column in columns:
                field = self._history_identity_field(model, column)
                clause = models.Q(**{column: field.value_from_object(self)})
                predicate = clause if predicate is None else predicate | clause
            scopes[model] = predicate if predicate is not None else models.Q(pk__in=[])
        return scopes


class HistoryReplayMixin(HistoryScopeMixin):
    class Meta(HistoryScopeMixin.Meta):
        abstract = True

    def admitted_reverts(self, changesets: Any) -> frozenset[Any]:
        scopes = self.history_scopes()
        admitted = set()
        for boundary in changesets:
            if boundary.state != "closed" or boundary.outcome != "success":
                continue
            present = False
            for model, history in boundary.history_rows():
                if not history.exists():
                    continue
                present = True
                if model not in scopes or history.exclude(scopes[model]).exists():
                    break
            else:
                if present:
                    admitted.add(boundary.pk)
        return frozenset(admitted)

    def revert_removal_collector(self) -> Collector | None:
        """Declare native deletion effects; retained-removal owners return None."""
        collector = Collector(using=self._state.db or "default", origin=self)
        collector.collect(cast(Any, [self]))
        return collector

    def revert_removal(self, *, reason: str = "") -> None:
        self.delete(using=self._state.db)

    def revert_subjects(self) -> list[Any]:
        return []

    def resolve_statements(self) -> None:
        """Extension hook for a subject with derived retained assertions."""


class ModelHistoryReplayMixin:
    @staticmethod
    def source_state_complete(*versions: Any) -> bool:
        """Whether every supplied snapshot retains its complete captured source state."""
        return all(row is None or row.history_source_complete for row in versions)

    @classmethod
    def require_complete_state(cls, *versions: Any) -> None:
        """Refuse legacy snapshots whose source values were never retained."""
        if not cls.source_state_complete(*versions):
            raise ReplayRefused("Historical source state is incomplete; lost values cannot be restored.")

    use_base_model_db: bool

    def source_database_attributes(self, history_model: type[models.Model], *, using: str) -> dict[str, str]:
        """Select source identity, or require explicitly co-located history bases.

        Custom bases without source_database require use_base_model_db=True:
        the actual history table alias then proves source identity without a
        new persisted field or an inferred legacy value.
        """
        if any(field.name == "source_database" for field in history_model._meta.concrete_fields):
            return {"source_database": using}
        if not self.use_base_model_db:
            raise ImproperlyConfigured("History without source_database requires use_base_model_db=True.")
        return {}

    def changeset_states(self, model: Any, changeset: Any, *, using: str) -> Any:
        history = get_history_manager_for_model(model).using(using)
        key = model._meta.pk.attname
        source = self.source_database_attributes(history.model, using=using)
        versions = history.filter(history_changeset_id=changeset.pk).order_by("history_id")
        groups: dict[Any, list[Any]] = {}
        for version in versions:
            if any(getattr(version, name) != value for name, value in source.items()):
                raise ReplayRefused("Replay requires source rows and retained history in the same database.")
            groups.setdefault(getattr(version, key), []).append(version)
        for pk, rows in groups.items():
            first, last = rows[0], rows[-1]
            timeline = history.filter(**source, **{key: pk})
            if timeline.filter(history_id__gt=last.history_id).exists():
                raise ReplayRefused(f"{model._meta.label} {pk}: a later write prevents replay.")
            previous = timeline.filter(history_id__lt=first.history_id).order_by("-history_id").first()
            if previous is None and first.history_type != "+":
                raise ReplayRefused(f"{model._meta.label} {pk}: prehistory is unavailable.")
            self.require_complete_state(first, last, previous)
            yield first, last, previous

    def lock_revert_subject(self, target: models.Model, *, using: str) -> None:
        """Lock the operational row through the host's native subject contract."""
        list(type(target)._base_manager.using(using).select_for_update().filter(pk=target.pk))

    def authorize_revert(self, first: Any, last: Any, previous: Any, actor: Any) -> None:
        snapshot = previous or last
        target = snapshot.history_object
        stored = type(target)._base_manager.using(first._state.db).filter(pk=target.pk).first()
        removal = previous is None or previous.history_type == "-"
        action = "delete" if removal else "add" if stored is None else "change"
        get_policy().authorize(actor, action, stored or target)
        if not removal:
            # Field-owned validation is read-only. Unique/FK validity remains a
            # database constraint inside the surrounding all-or-nothing replay.
            tracked = {field.attname for field in snapshot.tracked_fields}
            for field in target._meta.concrete_fields:
                if field.attname in tracked and not field.is_relation:
                    field.clean(getattr(target, field.attname), target)

    def revert_order(self, first: Any, last: Any, previous: Any) -> tuple[Any, ...]:
        removal = previous is None or previous.history_type == "-"
        row = first.history_object
        return (2 if removal else 0, row._meta.label_lower, str(row.pk))

    def revert_removal_collector(self, first: Any, last: Any, previous: Any) -> Collector | None:
        if previous is not None and previous.history_type != "-":
            return None
        target = first.history_object
        row = type(target)._base_manager.using(first._state.db).filter(pk=target.pk).first()
        if row is None:
            return None
        if isinstance(row, HistoryReplayMixin):
            return row.revert_removal_collector()
        collector = Collector(using=first._state.db, origin=row)
        collector.collect(cast(Any, [row]))
        return collector

    def validate_revert_removal(
        self, first: Any, last: Any, previous: Any, removals: set[tuple[type[models.Model], Any]]
    ) -> None:
        collector = self.revert_removal_collector(first, last, previous)
        if collector is None:
            return
        affected = {(model, row.pk) for model, rows in collector.data.items() for row in rows}
        for queryset in collector.fast_deletes:
            affected.update((queryset.model, pk) for pk in queryset.values_list("pk", flat=True))
        # SET_NULL and SET_DEFAULT affect retained dependents even when no row
        # is cascaded. Refuse those side effects unless those rows are removed.
        for (field, _value), batches in collector.field_updates.items():
            for batch in batches:
                affected.update((field.model, row.pk) for row in cast(Iterable[models.Model], batch))
        if affected - removals:
            raise ReplayRefused("Replay removal would affect rows outside this boundary.")

    def stage_revert(self, first: Any, last: Any, previous: Any) -> None:
        """Extension hook for retained owners with uniqueness staging."""

    def revert_row(self, first: Any, last: Any, previous: Any) -> list[Any]:
        target = first.history_object
        model, using = type(target), first._state.db
        row = model._base_manager.using(using).filter(pk=target.pk).first()
        if previous is None or previous.history_type == "-":
            if row is not None:
                if isinstance(row, HistoryReplayMixin):
                    row.revert_removal(reason=f"Revert changeset {first.history_changeset_id}")
                else:
                    row.delete(using=using)
            return []
        snapshot = previous.instance
        if row is None:
            snapshot.save(using=using)
            row = snapshot
        else:
            changed = []
            for field in previous.tracked_fields:
                if field.primary_key or (field.is_relation and field.remote_field.parent_link):
                    continue
                value = getattr(snapshot, field.attname)
                if getattr(row, field.attname) != value:
                    setattr(row, field.attname, value)
                    changed.append(field.name)
            if changed:
                row.save(using=using, update_fields=changed)
        return list(row.revert_subjects()) if isinstance(row, HistoryReplayMixin) else []


def ordered_states(states: list[Any]) -> list[Any]:
    """Order restoration and removal by actual FK targets, refusing cycles."""
    result: list[Any] = []
    for phase in sorted({state[0].revert_order(*state[1:])[0] for state in states}):
        batch = sorted(
            (state for state in states if state[0].revert_order(*state[1:])[0] == phase),
            key=lambda state: state[0].revert_order(*state[1:]),
        )
        rows = [(state[3] or state[2]).instance for state in batch]
        identities = {
            (type(row), field.name, getattr(row, field.attname)): index
            for index, row in enumerate(rows)
            for field in row._meta.concrete_fields
            if field.unique
        }
        graph: dict[int, set[int]] = {index: set() for index in range(len(batch))}
        for index, row in enumerate(rows):
            for field in row._meta.concrete_fields:
                if not isinstance(field, models.ForeignKey):
                    continue
                parent = identities.get(
                    (field.remote_field.model, field.target_field.name, getattr(row, field.attname))
                )
                if parent is not None and parent != index:
                    if phase == 2:
                        graph[parent].add(index)
                    elif batch[parent][2].history_type == "-" or issubclass(type(row), field.remote_field.model):
                        graph[index].add(parent)
        try:
            ordering = TopologicalSorter(graph)
            ordering.prepare()
            order = []
            while ordering.is_active():
                for index in sorted(ordering.get_ready()):
                    order.append(index)
                    ordering.done(index)
        except CycleError as error:
            raise ReplayRefused("Replay contains an unsupported foreign-key cycle.") from error
        result.extend(batch[index] for index in order)
    return result
