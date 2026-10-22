"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from itertools import groupby
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.base import ModelBase
from django.db.models.fields.reverse_related import ManyToOneRel
from django.utils import timezone
from rebac import (
    PermissionDenied,
    RebacMixin,
    SubjectRef,
    system_context,
)
from rebac.actors import is_sudo
from rebac.errors import SchemaError
from rebac.field_backing import resolve_field_backing
from rebac.field_visibility import gated_read_fields, redacted_fields
from rebac.resources import model_resource_type
from rebac.schema import ConstBinding, FieldBinding
from simple_history.manager import HistoryManager
from simple_history.models import HistoricalRecords
from simple_history.signals import pre_create_historical_record
from simple_history.utils import get_history_manager_for_model

from angee.actors import actor_user_id
from angee.changesets import changeset_model_label, current_changeset, write_group
from angee.lineage import concrete_child_models, concrete_lineage
from angee.models.query import AngeeQuerySet, by_key, candidate_relationships, model_history
from angee.models.trash import TrashMixin

_UNLOADED = object()


class ChangesetQuerySet[T: models.Model](AngeeQuerySet[T]):
    """Changeset scopes derived from the history contracts of records they touch."""

    def touching(self, record: HistoryMixin) -> Self:
        """Return changesets belonging to ``record``'s declared history document."""

        using = record._state.db
        rows = [
            get_history_manager_for_model(table).using(using)
            .filter(predicate, changeset_id__isnull=False)
            .order_by()
            .values_list("changeset_id", flat=True)
            for table, predicate in record.history_scopes().items()
        ]
        membership = Q(pk__in=models.Subquery(rows[0].union(*rows[1:]))) if rows else Q(pk__in=())
        return self.filter(membership | self._merge_touching(record))

    @staticmethod
    def _merge_touching(record: HistoryMixin) -> Q:
        content_types = [
            ContentType.objects.db_manager(hints={"instance": record}).get_for_model(table)
            for table in record.history_tables()
        ]
        return Q(
            activity="merge",
            origin_content_type__in=content_types,
            origin_object_id=str(record.pk),
        )

    def records_touched(self) -> Iterable[tuple[type[models.Model], Any]]:
        """Yield only identities of existing records whose history documents we touch.

        This is the inverse of ``touching``: the same history scopes cover MTI,
        owned lines, pairs and their statements, plus an unchanged merge survivor.
        SQL selects identities, never live/history bodies. The touched-model index
        avoids probing documents whose tables cannot intersect these boundaries.
        """

        touched = {
            label for labels in self.order_by().values_list("touched", flat=True) for label in labels
        }
        boundaries = self.order_by().values("pk")
        merges: dict[int, set[int]] = {}
        for content_type_id, pk in self.filter(activity="merge").values_list(
            "origin_content_type_id", "origin_object_id",
        ):
            if content_type_id is not None and pk is not None:
                merges.setdefault(content_type_id, set()).add(int(pk))
        identities: dict[tuple[type[models.Model], str], set[Any]] = {}

        def touched_ids(table: type[models.Model], column: str) -> set[Any]:
            key = table, column
            if key not in identities:
                identities[key] = set(get_history_manager_for_model(table).using(self.db).filter(
                    changeset_id__in=boundaries,
                ).order_by().values_list(column, flat=True))
            return identities[key]

        for model in sorted(apps.get_models(), key=lambda model: model._meta.label_lower):
            if not issubclass(model, HistoryMixin) or model_history(model) is None:
                continue
            record = model()
            ids: set[Any] = set()
            for table, columns in record.history_identity_columns().items():
                if table._meta.label_lower in touched:
                    for column in columns:
                        ids.update(touched_ids(table, column))
                for statement in getattr(table, "statement_models", lambda: ())():
                    if statement._meta.label_lower not in touched:
                        continue
                    subjects = touched_ids(statement, "subject_id")
                    if table in record.history_tables():
                        ids.update(subjects)
                    elif subjects:
                        # Deleted/moved owned rows still identify every document
                        # their statements belong to through retained snapshots.
                        history = get_history_manager_for_model(table).using(self.db).filter(**{
                            f"{table._meta.pk.attname}__in": subjects,
                        })
                        for column in columns:
                            ids.update(history.order_by().values_list(column, flat=True))
            if merges:
                content_types = ContentType.objects.db_manager(self.db).get_for_models(*record.history_tables())
                for content_type in content_types.values():
                    ids.update(merges.get(content_type.pk, ()))
            rows = model._base_manager.using(self.db).filter(pk__in=ids)
            yield from ((model, pk) for pk in rows.order_by("pk").values_list("pk", flat=True))


class _ChangesetHistory(models.Model):
    """Historical attribution shared by every native generated history model."""

    changeset = models.ForeignKey(
        changeset_model_label(),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True


class ModelHistoryManager(HistoryManager):
    """Native bulk construction extended to accept deletion snapshots."""

    def bulk_history_create(
        self,
        objs: Iterable[models.Model],
        batch_size: int | None = None,
        update: bool = False,
        default_user: models.Model | None = None,
        default_change_reason: str = "",
        default_date: datetime | None = None,
        custom_historical_attrs: dict[str, Any] | None = None,
        *,
        history_type: str | None = None,
    ) -> list[models.Model]:
        """Fill upstream 3.13's constructor-loop gap for explicit deletion history types."""

        if not getattr(settings, "SIMPLE_HISTORY_ENABLED", True):
            return []
        rows = []
        for instance in objs:
            attrs = {field.attname: getattr(instance, field.attname) for field in self.model.tracked_fields}
            attrs.update(custom_historical_attrs or {})
            row = self.model(
                history_date=getattr(instance, "_history_date", default_date or timezone.now()),
                history_user=default_user or self.model.get_default_history_user(instance),
                history_change_reason=default_change_reason,
                history_type=history_type or ("~" if update else "+"),
                **attrs,
            )
            if hasattr(self.model, "history_relation"):
                row.history_relation_id = instance.pk
            rows.append(row)
        return cast(list[models.Model], self.model.objects.using(self.db).bulk_create(rows, batch_size=batch_size))


class ModelHistory(HistoricalRecords):
    """Native history adapted to abstract sources and generated module names.

    Every concrete descendant of a tracked source owns a history table, including
    MTI children. Native history construction receives a detached
    snapshot with stored gated values, never the caller's redacted projection.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("history_manager", ModelHistoryManager)
        kwargs.setdefault("bases", (_ChangesetHistory,))
        super().__init__(*args, **kwargs)

    def contribute_to_class(self, cls: Any, name: str) -> None:
        super().contribute_to_class(cls, name)
        setattr(cls, name, self)

    def finalize(self, sender: type[models.Model], **kwargs: Any) -> None:
        if sender._meta.abstract or sender._meta.proxy or not issubclass(sender, self.cls):
            return
        declaration = next(
            (
                owner.__dict__[self.manager_name]
                for owner in sender.__mro__
                if isinstance(owner.__dict__.get(self.manager_name), ModelHistory)
            ),
            self,
        )
        if declaration is not self:
            return
        super().finalize(sender, **kwargs)
        setattr(sender._meta, "angee_model_history", self)
        pre_create_historical_record.connect(
            self.set_changeset,
            sender=get_history_manager_for_model(sender).model,
            weak=False,
        )

    def set_changeset(self, instance: models.Model, history_instance: _ChangesetHistory, **kwargs: Any) -> None:
        """Bind attribution after native construction and before the history save."""

        history_instance.changeset = current_changeset(required=True, record=True)

    def get_history_user(self, instance: models.Model) -> models.Model | None:
        """Derive the user FK from the changeset's native subject, without a lookup."""

        context = current_changeset()
        actor = SubjectRef.parse(context.actor_ref) if context is not None and context.actor_ref else None
        user_id = actor_user_id(actor)
        return get_user_model()(pk=user_id) if user_id is not None else None

    def get_change_reason_for_object(self, instance: models.Model, history_type: str, using: str | None) -> str:
        """The changeset owns the reason, including for native M2M history."""

        context = current_changeset()
        return context.change_reason if context is not None else ""

    def record_rows(self, model: type[models.Model], count: int, *, using: str) -> None:
        """Accumulate durable captures in memory; the boundary closes them once."""

        context = current_changeset()
        if context is None:
            return
        label = model._meta.label_lower

        def committed() -> None:
            context.touched.append(label)
            context.counts[label] = context.counts.get(label, 0) + count

        transaction.on_commit(committed, using=using)

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
        if (
            changeset.activity == "merge"
            and origin.model_label.lower() in {table._meta.label_lower for table in concrete_lineage(model)}
        ):
            pk = model._meta.pk.to_python(origin.object_id)
            if pk not in seen and model._base_manager.using(using).filter(pk=pk).exists():
                timeline = history.filter(**{key: pk})
                boundary = timeline.order_by("-history_id").first()
                if boundary is None:
                    raise ValidationError(f"{model._meta.label} {pk}: the pre-boundary state is unavailable.")
                if timeline.filter(history_date__gt=changeset.recorded_at).exists():
                    raise ValidationError(f"{model._meta.label} {pk}: a later write prevents revert.")
                yield boundary, boundary, boundary

    def diff(self, first: Any, last: Any, *, previous: Any = _UNLOADED) -> Any:
        """Return the native field delta across one changeset boundary.

        Page assemblers pass their already loaded predecessor. Standalone callers
        retain the same owner contract and let the history table resolve it.
        """

        target = last.history_object
        model = type(target)
        key = model._meta.pk.attname
        if previous is _UNLOADED:
            previous = (
                type(last).objects.using(last._state.db)
                .filter(**{key: getattr(last, key)}, history_id__lt=first.history_id)
                .order_by("-history_id")
                .first()
            )
        fields = {
            field.name: field
            for field in model._meta.concrete_fields
            if field.editable
            and not field.primary_key
            and not (field.remote_field and field.remote_field.parent_link)
        }
        included = [field.name for field in last.tracked_fields if field.name in fields]
        deleted = last.history_type == "-"
        if deleted and (
            first.history_type == "+"
            and previous is None
            or previous is not None
            and previous.history_type == "-"
        ):
            included = []
            previous = current = last
        elif deleted:
            previous = previous if previous is not None else last
            current = type(last)(**{fields[name].attname: None for name in included})
        else:
            previous = previous if previous is not None else type(last)()
            current = last
        return current.diff_against(previous, included_fields=included)

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
            name
            for name in gated_read_fields(model)
            if not self.snapshot_allows(version, f"read__{name}", actor=actor)
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
                    if not resolved.target_model._base_manager.using(snapshot._state.db).filter(
                        **{resolved.target_id_attr: subject.subject_id},
                    ).exists():
                        relations[relation.name] = None
                        break
            return candidate.allows(actor, action)
        except (
            FieldDoesNotExist, ImproperlyConfigured, ObjectDoesNotExist,
            SchemaError, ValidationError,
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
                if not field.primary_key and field.attname in tracked
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

    def bulk_history_create(
        self,
        model: type[models.Model],
        keys: Iterable[Any],
        history_type: str,
        *,
        using: str,
        batch_size: int | None = None,
    ) -> None:
        """Batch stored snapshots of this model and its tracked MTI descendants.

        Each executed batch records pending receiver keys, never suppressing a
        later queryset write in the group. Native receivers consume those keys.
        """

        if not getattr(settings, "SIMPLE_HISTORY_ENABLED", True):
            return
        manager = get_history_manager_for_model(model)
        with write_group() as group:
            keys = list(dict.fromkeys(keys))
            names = [field.attname for field in self.fields_included(model)]
            history_using = using if self.use_base_model_db else manager.db
            for rows in by_key(model._base_manager.using(using), keys):
                snapshots = list(rows.only(*names).order_by())
                if not snapshots:
                    continue
                context = current_changeset(required=True, record=True)
                records = manager.db_manager(history_using).bulk_history_create(
                    snapshots,
                    batch_size=batch_size,
                    history_type=history_type,
                    default_user=self.get_history_user(snapshots[0]),
                    default_change_reason=self.get_change_reason_for_object(snapshots[0], history_type, using),
                    custom_historical_attrs={"changeset": context},
                )
                group.seen.update((model._meta.label_lower, snapshot.pk, history_type) for snapshot in snapshots)
                for record, snapshot in zip(records, snapshots, strict=True):
                    self.create_historical_record_m2ms(record, snapshot)
                self.record_rows(model, len(records), using=history_using)
            for child in concrete_child_models(model):
                history = model_history(child)
                if history is not None:
                    history.bulk_history_create(child, keys, history_type, using=using, batch_size=batch_size)

    def create_historical_record(self, instance: models.Model, history_type: str, using: str | None = None) -> None:
        """Keep native signals and M2M capture on the same stored-snapshot writer."""

        model = type(instance)
        key = (model._meta.label_lower, instance.pk, history_type)
        with write_group() as group:
            if key in group.seen:
                group.seen.remove(key)
                return
            current_changeset(required=True, record=True)
            alias = model._base_manager.db_manager(hints={"instance": instance}).db
            names = [field.attname for field in self.fields_included(model)]
            try:
                snapshot = model._base_manager.using(alias).only(*names).get(pk=instance.pk)
            except ObjectDoesNotExist as error:
                if history_type == "-":
                    # Django's collector sends pre_delete for every collected row before
                    # deleting any; a receiver on an earlier row (messaging's chatter
                    # teardown) may already have deleted this one through its own capturing
                    # delete, which wrote the "-" snapshot. Nothing is left to capture.
                    return
                raise type(error)(f"Cannot capture missing {model._meta.label} row {instance.pk!r}.") from error
            if hasattr(instance, "_history_date"):
                setattr(snapshot, "_history_date", instance._history_date)
            super().create_historical_record(snapshot, history_type, using=alias)
            history_manager = get_history_manager_for_model(model)
            history_using = alias if self.use_base_model_db else history_manager.db
            self.record_rows(model, 1, using=history_using)

    def pre_delete(self, instance: models.Model, **kwargs: Any) -> None:
        """Snapshot while stored values exist, inside Django's delete transaction."""

        super().pre_delete(instance, **kwargs)
        if getattr(settings, "SIMPLE_HISTORY_ENABLED", True) and not self.cascade_delete_history:
            self.create_historical_record(instance, "-", using=kwargs.get("using"))

    def post_delete(self, instance: models.Model, using: str | None = None, **kwargs: Any) -> None:
        """Retain native cascade cleanup; retained snapshots were written before deletion."""

        if self.cascade_delete_history:
            super().post_delete(instance, using=using, **kwargs)

    def get_meta_options(self, model: type[models.Model]) -> dict[str, Any]:
        options = super().get_meta_options(model)
        options["app_label"] = model._meta.app_label
        return options

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        """Keep global attribution storage resolvable in isolated model registries."""

        extra = super().get_extra_fields(model, fields)
        if model._meta.apps is not apps:
            if self.user_id_field is None and self.user_model is None:
                user = get_user_model()
                extra["history_user"].remote_field.model = user
                extra["history_user"].remote_field.field_name = user._meta.pk.name
            field = _ChangesetHistory._meta.get_field("changeset").clone()
            field.remote_field.model = apps.get_model(changeset_model_label())
            field.remote_field.field_name = field.remote_field.model._meta.pk.name
            extra["changeset"] = field
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

    def copy_fields(self, model: type[models.Model]) -> dict[str, models.Field]:
        """Copy MTI identity as a regular historical relation, not an inheritance link."""

        fields = super().copy_fields(model)
        for name, field in fields.items():
            source = model._meta.get_field(name)
            if not isinstance(source, models.ForeignKey):
                continue
            assert isinstance(field, models.ForeignKey)
            related = source.remote_field.model
            if model._meta.apps is not apps and isinstance(related, type) and related._meta.apps is apps:
                field.remote_field.model = related
                field.remote_field.field_name = source.target_field.name
            if source.auto_created and source.remote_field.parent_link:
                field.auto_created = False
                field.remote_field.parent_link = False
        return fields

    def fields_included(self, model: type[models.Model]) -> list[models.Field]:
        """Keep snapshot fields, excluding virtual and row-derived generated columns."""

        return [
            field
            for field in super().fields_included(model)
            if (field.concrete or field.is_relation or field.auto_created)
            and not isinstance(field, models.GeneratedField)
        ]


class HistoryMixin(models.Model):
    """Track concrete models through django-simple-history's inherited descriptor."""

    history = ModelHistory(inherit=True)
    if TYPE_CHECKING:
        save_without_historical_record = models.Model.save

    history_revert_permission: ClassVar[str] = "write"
    """Permission admitting replay; immutable edges use their unlink/relink authority."""
    history_relations: ClassVar[tuple[str, ...]] = ()
    """Owned reverse foreign-key collections included in this document's history.

    These are document parts, not arbitrary references or separately owned work.
    Each child keeps its own capture and permission policy.
    """

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
            pair_models = getattr(owner, "pair_models", None)
            if pair_models is None:
                continue
            for pair in pair_models():
                columns[pair] = (*columns.get(pair, ()), "left_id", "right_id")
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
        statements: dict[type[models.Model], Q] = {}
        tables = frozenset(self.history_tables())
        for model, predicate in scopes.items():
            statement_models = getattr(model, "statement_models", None)
            if statement_models is None:
                continue
            subjects = get_history_manager_for_model(model).using(self._state.db).filter(predicate).values(
                model._meta.pk.attname,
            )
            subject_scope = (
                Q(subject_id=self.pk)
                if model in tables
                else Q(subject_id__in=models.Subquery(subjects))
            )
            for statement in statement_models():
                statements[statement] = (
                    statements[statement] | subject_scope
                    if statement in statements
                    else subject_scope
                )
        return {**scopes, **statements}

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

    def save_base(
        self,
        raw: bool = False,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        """Validate attribution before SQL and scope native save dispatch to one group."""

        manager = type(self)._base_manager.db_manager(hints={"instance": self})
        alias = manager.db
        with transaction.atomic(using=alias, savepoint=not raw), write_group():
            if not raw and not hasattr(self, "skip_history_when_saving"):
                if model_history(type(self)) is not None:
                    current_changeset(required=True, record=True)
            super().save_base(raw, force_insert, force_update, alias, update_fields)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Keep instance and collector receivers in one ephemeral write group."""

        history = model_history(type(self))
        if history is not None and not history.cascade_delete_history:
            current_changeset(required=True, record=True)
        with write_group():
            return super().delete(*args, **kwargs)

    def revert_subjects(self) -> list[Any]:
        """Rows whose derived state re-runs after a revert; ordinary rows have none."""

        return []
