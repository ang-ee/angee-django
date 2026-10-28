"""Angee permission, replay, and document policies over native provenance history."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from itertools import groupby
from typing import TYPE_CHECKING, Any, ClassVar, Self

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.fields.reverse_related import ManyToOneRel
from rebac import (
    PermissionDenied,
    RebacMixin,
    system_context,
)
from rebac.actors import is_sudo
from rebac.errors import SchemaError
from rebac.field_backing import resolve_field_backing
from rebac.field_visibility import gated_read_fields, redacted_fields
from rebac.resources import model_resource_type
from rebac.schema import ConstBinding, FieldBinding
from simple_history.exceptions import NotHistoricalModelError
from simple_history.manager import HistoryManager
from simple_history.utils import get_history_manager_for_model

from angee.base.lineage import concrete_child_models, concrete_lineage
from angee.base.managers import AngeeManager, AngeeUnscopedManager
from angee.base.models.merge_references import MergeContractMixin
from angee.base.models.trash import TrashMixin
from angee.base.querysets import SystemQuerysetMixin, candidate_relationships
from angee.provenance.contrib.angee.managers import AngeeHistoryManager, AngeeHistoryUnscopedManager
from angee.provenance.history import (
    _UNLOADED,
)
from angee.provenance.history import (
    HistoryMixin as CaptureHistoryMixin,
)
from angee.provenance.history import (
    ModelHistory as CaptureHistory,
)
from angee.provenance.querysets import model_history


class HistoryDocumentMixin(models.Model):
    """Noncapturing document-history extension contract for sibling capabilities.

    Declaring document contributions does not install a history descriptor or
    capture managers. HistoryMixin opts into capture separately.
    """

    class Meta:
        abstract = True

    @classmethod
    def history_related_columns(cls) -> dict[type[models.Model], tuple[str, ...]]:
        """Related snapshot columns that identify this document's record."""

        return {}

    @classmethod
    def history_related_scopes(
        cls, predicate: Q, root_pk: Any, *, direct: bool, using: str | None,
    ) -> dict[type[models.Model], Q]:
        """Predicates on owned representations admitted with this document."""

        return {}


class HistoricalReferenceMixin(MergeContractMixin):
    """Native historical snapshots retain identity without blocking creation undo."""

    class Meta:
        abstract = True

    @classmethod
    def blocks_target_delete(cls, target: type[models.Model]) -> bool:
        return False


def record_history(model: type[models.Model]) -> HistoryManager | None:
    """Read captured record history only through its own native REBAC identity."""

    if not model_resource_type(model):
        return None
    try:
        return get_history_manager_for_model(model._meta.concrete_model or model)
    except NotHistoricalModelError:
        return None


class ModelHistory(CaptureHistory):
    """Native history adapted to abstract sources and generated module names.

    Every concrete descendant of a tracked source owns a history table, including
    MTI children. Native history construction receives a detached
    snapshot with stored gated values, never the caller's redacted projection.
    """

    bases: tuple[type[models.Model], ...]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.bases = (*self.bases, HistoricalReferenceMixin)

    def capture_children(self, model: type[models.Model]) -> tuple[type[models.Model], ...]:
        """Share Angee's structural lineage owner for composed and isolated models."""

        return concrete_child_models(model)

    def changeset_states(self, model: Any, changeset: Any, *, using: str) -> Iterable[tuple[Any, Any, Any]]:
        """Yield first, last and pre-boundary versions; sequence IDs break date ties.

        A later write, including an unattributed one, refuses the whole revert.
        The caller holds the operational row locks throughout restoration.
        """

        history = model.history.using(using)
        key = model._meta.pk.attname
        rows = history.filter(changeset_id=changeset.pk).order_by(key, "history_id")
        seen = set()
        for pk, grouped in groupby(rows.iterator(), key=lambda row: getattr(row, key)):
            seen.add(pk)
            versions = list(grouped)
            first, last = versions[0], versions[-1]
            timeline = history.filter(**{key: pk})
            if timeline.filter(history_id__gt=last.history_id).exists():
                raise ValidationError(f"{model._meta.label} {pk}: a later write prevents revert.")
            previous = timeline.filter(history_id__lt=first.history_id).order_by("-history_id").first()
            if previous is None and first.history_type != "+":
                raise ValidationError(f"{model._meta.label} {pk}: the pre-boundary state is unavailable.")
            yield first, last, previous
        # An unchanged merge survivor has no new snapshot. Its touched owner and
        # origin still define a boundary, without an artificial stamp-only save.
        origin = changeset.record_ref
        if changeset.activity == "merge" and origin.model_label.lower() in {
            table._meta.label_lower for table in concrete_lineage(model)
        }:
            pk = model._meta.pk.to_python(origin.object_id)
            if pk not in seen and model._base_manager.using(using).filter(pk=pk).exists():
                timeline = history.filter(**{key: pk})
                boundary = timeline.order_by("-history_id").first()
                if boundary is None:
                    raise ValidationError(f"{model._meta.label} {pk}: the pre-boundary state is unavailable.")
                if timeline.filter(history_date__gt=changeset.recorded_at).exists():
                    raise ValidationError(f"{model._meta.label} {pk}: a later write prevents revert.")
                yield boundary, boundary, boundary

    def redacted(
        self,
        version: Any,
        actor: Any,
        *,
        current: Any = _UNLOADED,
        retained: bool | object = _UNLOADED,
    ) -> frozenset[str]:
        """Return fields hidden by the row's current or retained snapshot policy.

        A page may supply its bulk-loaded current row and retention decision so
        this owner does not probe those facts again for every retained version.
        """

        target = version.history_object
        model = type(target)
        using = version._state.db
        row = current
        if row is _UNLOADED:
            row = model.objects.with_actor(actor).with_action("read").using(using).filter(pk=target.pk).first()
        if row is not None:
            return redacted_fields(row)
        hidden = frozenset(field.name for field in model._meta.concrete_fields)
        if retained is _UNLOADED:
            retained = model._base_manager.using(using).filter(pk=target.pk).exists()
        if retained:
            return hidden
        if not self.snapshot_allows(version, "read", actor=actor):
            return hidden
        return frozenset(
            name for name in gated_read_fields(model) if not self.snapshot_allows(version, f"read__{name}", actor=actor)
        )

    def snapshot_allows(self, snapshot: Any, action: str, *, actor: Any) -> bool:
        """Check retained relationship facts through native candidate permissions.

        Unlike ``instance``, native ``history_object`` does not refill excluded
        fields from a current row. Omitted columns remain unresolved facts, never
        constructor defaults that might accidentally grant access. This preflight
        cannot reproduce a deleted row's stored grants; current rows must use
        their ordinary permission owner before considering a snapshot.
        """

        if is_sudo():
            return True
        if actor is None:
            return False
        target = snapshot.history_object
        resource = model_resource_type(type(target))
        if resource is None:
            return True
        tracked = {field.attname for field in snapshot.tracked_fields}
        for field in target._meta.concrete_fields:
            if field.attname not in tracked:
                setattr(target, field.attname, F(field.attname))
        try:
            candidate = candidate_relationships(target, action)
            if candidate is None:
                return False
            definition, required, relations = candidate.definition, candidate.required, candidate.relationships
            for relation in definition.relations:
                if relation.name not in required or isinstance(relation.backing, ConstBinding):
                    continue
                # New-row preflight knows an absent ledger or reverse relation
                # is empty. A retained snapshot cannot prove that former fact.
                if not isinstance(relation.backing, FieldBinding):
                    relations[relation.name] = None
                    continue
                resolved = resolve_field_backing(definition, relation)
                if resolved is None:
                    return False
                first = target._meta.get_field(resolved.path.split("__", 1)[0])
                if resolved.generic is None and not isinstance(first, (models.ForeignKey, models.OneToOneField)):
                    relations[relation.name] = None
                    continue
                # Native preflight trusts an inserted FK's constraint. A history
                # column retains the identity after the target itself is gone.
                for subject in relations.get(relation.name) or ():
                    if (
                        not resolved.target_model._base_manager.using(snapshot._state.db)
                        .filter(
                            **{resolved.target_id_attr: subject.subject_id},
                        )
                        .exists()
                    ):
                        relations[relation.name] = None
                        break
            return candidate.allows(actor, action)
        except (
            FieldDoesNotExist,
            ImproperlyConfigured,
            ObjectDoesNotExist,
            SchemaError,
            ValidationError,
        ):
            # Retained evidence can outlive a relationship target or its schema.
            # Neither grants access when native preflight cannot resolve it.
            return False

    def authorize_revert(self, first: Any, last: Any, previous: Any, actor: Any) -> None:
        """Admit both the current row and the snapshot's proposed relationships."""

        snapshot = previous or last
        target = snapshot.history_object
        action = target.history_revert_permission
        stored = type(target)._base_manager.using(first._state.db).filter(pk=target.pk).first()
        if stored is not None:
            stored.require_access(action, actor=actor)
            if action == "write":
                # Existing records may be writable through stored grants which
                # an id-less proposed-relationship check cannot reproduce.
                return
        if not self.snapshot_allows(snapshot, action, actor=actor):
            raise PermissionDenied(f"{target._meta.label} {target.pk}: restoration requires {action} on its snapshot.")

    def revert_order(self, first: Any, last: Any, previous: Any) -> tuple[Any, ...]:
        """Restore parents first; remove creations after reference replay, children first."""

        model = type(first.instance)
        removal = previous is None or previous.history_type == "-"
        depth = len(model._meta.get_parent_list())
        return (2 if removal else 0, -depth if removal else depth, model._meta.label_lower, first.pk)

    def stage_revert(self, first: Any, last: Any, previous: Any) -> None:
        """Ordinary rows need no uniqueness staging."""

    def revert_row(self, first: Any, last: Any, previous: Any) -> list[Any]:
        """Replay one exact native predecessor onto every column its history tracks.

        A multi-table child's save records only the child's history, so its version
        carries the inherited columns too and restores them; a parent level that
        recorded its own version of the same write restores the same values.
        """

        model = type(first.instance)
        using = first._state.db
        row = model._base_manager.using(using).filter(pk=first.instance.pk).first()
        if previous is None or previous.history_type == "-":
            if row is not None:
                row.revert_removal(reason=f"Revert changeset {first.changeset_id}")
            return []
        restored = previous.instance
        if row is None:
            for parent in model._meta.get_parent_list():
                stored = parent._base_manager.using(using).filter(pk=restored.pk).first()
                if stored is None:
                    raise ValidationError(f"{parent._meta.label} {restored.pk}: the MTI parent is unavailable.")
                for field in parent._meta.local_concrete_fields:
                    setattr(restored, field.attname, getattr(stored, field.attname))
            restored.save(using=using)
            row = restored
        else:
            # Compare the stored snapshots directly: native ``diff_against`` reads
            # editable fields only, and a revert restores trash and audit columns too.
            tracked = {field.attname for field in previous.tracked_fields}
            changed = {
                field.name
                for field in model._meta.concrete_fields
                if not field.primary_key
                and field.attname in tracked
                and not (field.is_relation and field.remote_field.parent_link)
                and getattr(last, field.attname) != getattr(previous, field.attname)
            }
            if isinstance(row, TrashMixin) and "is_trashed" in changed:
                row.revert_removal(previous)
                changed.difference_update(row.TRASH_FIELDS)
            for name in changed:
                field = model._meta.get_field(name)
                setattr(row, field.attname, getattr(previous, field.attname))
            if changed:
                row.save(using=using, update_fields=sorted(changed))
        return row.revert_subjects()

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        """Keep global attribution storage resolvable in isolated model registries."""

        extra = super().get_extra_fields(model, fields)
        native_instance = extra["instance"].fget

        def get_instance(record: Any) -> Any:
            # Native construction fills excluded columns (counters) from the stored
            # row through the default manager. That read rebuilds a snapshot for
            # replay, not a projection to the caller: a gated counter would refuse it.
            if not record._history_excluded_fields:
                return native_instance(record)
            with system_context(reason="angee.history.instance"):
                return native_instance(record)

        extra["instance"] = property(get_instance)
        return extra


class HistoryMixin(CaptureHistoryMixin, SystemQuerysetMixin, HistoryDocumentMixin):
    """Track concrete models through django-simple-history's inherited descriptor."""

    history = ModelHistory(inherit=True)
    objects: ClassVar[models.Manager[Self]] = AngeeHistoryManager()
    _angee_base: ClassVar[models.Manager[Self]] = AngeeHistoryUnscopedManager()
    _base_manager: ClassVar[AngeeUnscopedManager[Self]]
    _default_manager: ClassVar[AngeeManager[Self]]
    if TYPE_CHECKING:
        save_without_historical_record = models.Model.save

    history_revert_permission: ClassVar[str] = "write"
    """Permission admitting replay; immutable edges use their unlink/relink authority."""
    history_relations: ClassVar[tuple[str, ...]] = ()
    """Owned reverse foreign-key collections included in this document's history.

    These are document parts, not arbitrary references or separately owned work.
    Each child keeps its own capture and permission policy.
    """

    @classmethod
    def retained_reference_exists(cls, record: models.Model, lookup: Mapping[str, Any]) -> bool:
        """Include stored pointer versions even while capture is disabled."""

        return get_history_manager_for_model(cls).db_manager(hints={"instance": record}).filter(**lookup).exists()

    @classmethod
    @contextmanager
    def initialize_rows(cls, rows: Sequence[models.Model], *, using: str) -> Iterator[bool]:
        """Capture one complete creation after all model-owned derived values land."""

        history = model_history(cls)
        if history is None:
            with super().initialize_rows(rows, using=using) as deferred:
                yield deferred
            return
        absent = object()
        prior = [(row, row.__dict__.get("skip_history_when_saving", absent)) for row in rows]
        for row, _previous in prior:
            row.__dict__["skip_history_when_saving"] = True
        try:
            with super().initialize_rows(rows, using=using):
                yield True
        finally:
            for row, previous in prior:
                if previous is absent:
                    row.__dict__.pop("skip_history_when_saving", None)
                else:
                    row.__dict__["skip_history_when_saving"] = previous
        if rows:
            get_history_manager_for_model(cls).db_manager(using).bulk_history_create(rows)

    def history_tables(self) -> tuple[type[models.Model], ...]:
        """Return the concrete tables participating in this record's history."""

        return tuple(table for table in concrete_lineage(type(self)) if model_history(table) is not None)

    def history_identity_columns(self) -> dict[type[models.Model], tuple[str, ...]]:
        """Columns in each retained history table identifying this document.

        Both the forward document scope and its changeset inverse compose this
        declaration, including concrete ancestors, owned rows and pair endpoints.
        """

        columns: dict[type[models.Model], tuple[str, ...]] = {
            table: (table._meta.pk.attname,) for table in self.history_tables()
        }
        for name in self.history_relations:
            relation = self._meta.get_field(name)
            if not isinstance(relation, ManyToOneRel) or relation.field.remote_field.parent_link:
                raise ImproperlyConfigured(f"{self._meta.label}.{name}: history requires an owned reverse FK.")
            child = relation.related_model
            assert child is not None
            if model_history(child) is None:
                raise ImproperlyConfigured(f"{child._meta.label}: an owned history relation must keep history.")
            columns[child] = (*columns.get(child, ()), relation.field.attname)
        for owner in self.history_tables():
            if issubclass(owner, HistoryDocumentMixin):
                for related, names in owner.history_related_columns().items():
                    columns[related] = (*columns.get(related, ()), *names)
        return columns

    def history_scope(self) -> dict[type[models.Model], Q]:
        """Snapshot predicates for this record's concrete tables and owned lines."""

        scopes = {}
        for table, columns in self.history_identity_columns().items():
            scope = Q()
            for column in columns:
                scope |= Q(**{column: self.pk})
            scopes[table] = scope
        return scopes

    def history_scopes(self) -> dict[type[models.Model], Q]:
        """Return document and generated-statement predicates for this record."""

        scopes = self.history_scope()
        related: dict[type[models.Model], Q] = {}
        tables = frozenset(self.history_tables())
        for model, predicate in scopes.items():
            if not issubclass(model, HistoryDocumentMixin):
                continue
            for representation, scope in model.history_related_scopes(
                predicate, self.pk, direct=model in tables, using=self._state.db,
            ).items():
                related[representation] = related[representation] | scope if representation in related else scope
        return {**scopes, **related}


    def admitted_reverts(self, changesets: Iterable[Any]) -> frozenset[Any]:
        """Return page changeset keys belonging wholly to this history document.

        Each touched history table reports all page boundaries in one grouped
        query. A boundary is admitted only when it has at least one stored row
        and every stored row belongs to this record's declared document.
        """

        page = {
            changeset.pk: changeset
            for changeset in changesets
            if changeset._state.db == self._state.db
            and changeset.state == changeset.State.CLOSED
            and changeset.outcome == changeset.Outcome.SUCCESS
        }
        table_labels = {table._meta.label_lower for table in self.history_tables()}
        admitted = {
            changeset.pk
            for changeset in page.values()
            if changeset.activity == changeset.Activity.MERGE
            and changeset.record_ref.model_label.lower() in table_labels
            and str(changeset.record_ref.object_id) == str(self.pk)
        }
        candidates = {pk: changeset for pk, changeset in page.items() if pk not in admitted}
        scopes = self.history_scopes()
        touched: set[Any] = set()
        refused: set[Any] = set()
        by_label: dict[str, set[Any]] = {}
        for changeset in candidates.values():
            for label in set(changeset.touched):
                by_label.setdefault(label, set()).add(changeset.pk)
        for label, changeset_ids in sorted(by_label.items()):
            model = apps.get_model(label)
            if model_history(model) is None:
                raise ValidationError(f"{label}: the history owner is unavailable.")
            rows = model.history.using(self._state.db).filter(changeset_id__in=changeset_ids)
            predicate = scopes.get(model)
            if predicate is None:
                present = set(rows.values_list("changeset_id", flat=True).distinct())
                touched.update(present)
                refused.update(present)
                continue
            for totals in rows.values("changeset_id").annotate(
                total=models.Count("history_id"),
                admitted=models.Count("history_id", filter=predicate),
            ):
                changeset_id = totals["changeset_id"]
                touched.add(changeset_id)
                if totals["total"] != totals["admitted"]:
                    refused.add(changeset_id)
        admitted.update(pk for pk in candidates if pk in touched and pk not in refused)
        return frozenset(admitted)

    def admits_revert(self, changeset: Any) -> bool:
        """Return whether one successful changeset belongs wholly to this document."""

        return changeset.pk in self.admitted_reverts((changeset,))

    def revertable_changesets(self, changesets: Iterable[Any], *, actor: Any = None) -> frozenset[Any]:
        """Return admitted page keys when the reader may replay this record."""

        admitted = self.admitted_reverts(changesets)
        if not admitted:
            return admitted
        if not isinstance(self, RebacMixin):
            raise ImproperlyConfigured("History replay admission requires RebacMixin.")
        if actor is not None:
            self.with_actor(actor)
        return admitted if self.has_access(self.history_revert_permission) else frozenset()

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"

    def revert_subjects(self) -> list[Any]:
        """Rows whose derived state re-runs after a revert; ordinary rows have none."""

        return []
