"""Native simple-history registration and stored-state capture."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db import models, router, transaction
from django.db.models.base import ModelBase
from django.db.models.signals import pre_save
from django.utils import timezone
from django.utils.functional import classproperty
from simple_history.manager import HistoryManager
from simple_history.models import HistoricalChanges, HistoricalRecords
from simple_history.signals import pre_create_historical_record
from simple_history.utils import get_change_reason_from_object, get_history_manager_for_model

from angee.provenance.engine.replay import HistoryReplayMixin, ModelHistoryReplayMixin

from .context import changeset_model_label, current_changeset, write_group
from .querysets import HistoryQuerySet, model_history

_UNLOADED = object()


class HistoryAttribution(models.Model):
    """Capture metadata independent of the historical model's copied source state."""

    history_changeset = models.ForeignKey(
        changeset_model_label(), null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    history_source_complete = models.BooleanField(default=False, editable=False)

    class Meta:
        abstract = True


class _Attribution(HistoryAttribution):
    source_database = models.CharField(max_length=255, default="default", editable=False)

    class Meta(HistoryAttribution.Meta):
        abstract = True


def _metadata(source: models.Model, target: models.Model) -> None:
    for name in ("_history_user", "_history_date", "_change_reason"):
        if hasattr(source, name):
            setattr(target, name, getattr(source, name))


class ModelHistoryManager[T: HistoricalChanges](HistoryManager[T]):
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
        grouped: dict[str, list[models.Model]] = {}
        for obj in objs:
            if obj.pk is None:
                raise ValueError("Historical snapshots require persisted primary keys.")
            alias = obj._state.db or router.db_for_write(type(obj), instance=obj)
            grouped.setdefault(alias, []).append(obj)
        records: list[models.Model] = []
        for alias, objects in grouped.items():
            records.extend(
                self.model._provenance_history.bulk_history_create(
                    self.model.instance_type,
                    [obj.pk for obj in objects],
                    history_type or ("~" if update else "+"),
                    using=alias,
                    history_using=self._db,
                    instances={obj.pk: obj for obj in objects},
                    batch_size=batch_size,
                    default_user=default_user,
                    default_change_reason=default_change_reason,
                    default_date=default_date,
                    custom_historical_attrs=custom_historical_attrs,
                )
            )
        return records

    def _construct_history(
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
        """Extend upstream's bulk constructor with the native deletion marker."""
        if not getattr(settings, "SIMPLE_HISTORY_ENABLED", True):
            return []
        rows = []
        for obj in objs:
            values = {field.attname: getattr(obj, field.attname) for field in self.model.tracked_fields}
            rows.append(
                self.model(
                    **values,
                    **(custom_historical_attrs or {}),
                    history_type=history_type or ("~" if update else "+"),
                    history_date=getattr(obj, "_history_date", default_date or timezone.now()),
                    history_user=getattr(
                        obj,
                        "_history_user",
                        default_user or self.model.get_default_history_user(obj),
                    ),
                    history_change_reason=get_change_reason_from_object(obj) or default_change_reason,
                )
            )
            if hasattr(self.model, "history_relation"):
                rows[-1].history_relation_id = obj.pk
        return cast(
            list[models.Model],
            self.model.objects.using(self.db).bulk_create(rows, batch_size=batch_size),
        )


class ModelHistory(ModelHistoryReplayMixin, HistoricalRecords):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("history_manager", ModelHistoryManager)
        kwargs.setdefault("bases", (_Attribution,))
        super().__init__(*args, **kwargs)

    @property
    def capture_enabled(self) -> bool:
        return bool(getattr(settings, "SIMPLE_HISTORY_ENABLED", True))

    def contribute_to_class(self, cls: Any, name: str) -> None:
        super().contribute_to_class(cls, name)
        setattr(cls, name, self)

    def finalize(self, sender: type[models.Model], **kwargs: Any) -> None:
        if sender._meta.abstract or sender._meta.proxy or not issubclass(sender, self.cls):
            return
        declaration = next(
            (
                base.__dict__[self.manager_name]
                for base in sender.__mro__
                if isinstance(base.__dict__.get(self.manager_name), ModelHistory)
            ),
            self,
        )
        if declaration is not self:
            return
        for role, manager in (("base", sender._base_manager), ("default", sender._default_manager)):
            if not isinstance(manager.get_queryset(), HistoryQuerySet):
                raise ImproperlyConfigured(f"{sender._meta.label} requires a HistoryQuerySet {role} manager.")
        super().finalize(sender, **kwargs)
        history_model = get_history_manager_for_model(sender).model
        if not issubclass(history_model, HistoryAttribution):
            raise ImproperlyConfigured("ModelHistory bases must compose HistoryAttribution.")
        self.source_database_attributes(history_model, using="default")
        pre_save.connect(self.require_before_save, sender=sender, weak=False)
        pre_create_historical_record.connect(
            self.set_changeset, sender=get_history_manager_for_model(sender).model, weak=False
        )

    def require_before_save(self, instance: models.Model, using: str, raw: bool = False, **kwargs: Any) -> None:
        if self.capture_enabled and not raw and not hasattr(instance, "skip_history_when_saving"):
            current_changeset(required=True, using=self.destination(type(instance), using))

    def destination(self, model: type[models.Model], using: str, explicit: str | None = None) -> str:
        history_model = get_history_manager_for_model(model._meta.concrete_model).model
        if (
            explicit is not None
            and explicit != using
            and not self.source_database_attributes(history_model, using=using)
        ):
            raise ValueError("History without source_database must remain on its source database.")
        return explicit or (using if self.use_base_model_db else router.db_for_write(history_model))

    def require_context(self, using: str) -> Any:
        return current_changeset(required=True, record=True, using=using)

    def set_changeset(
        self,
        instance: models.Model,
        history_instance: Any,
        using: str | None = None,
        **kwargs: Any,
    ) -> None:
        alias = self.destination(type(instance), instance._state.db or "default", using)
        context = self.require_context(alias)
        history_instance.history_changeset_id = context.pk if context is not None else None
        history_instance.history_source_complete = True
        for name, value in self.source_database_attributes(
            type(history_instance), using=instance._state.db or "default"
        ).items():
            setattr(history_instance, name, value)

    def get_change_reason_for_object(self, instance: models.Model, history_type: str, using: str | None) -> str:
        context = current_changeset(using=using)
        return get_change_reason_from_object(instance) or (context.change_reason if context else "")

    def get_history_user(self, instance: models.Model) -> models.Model | None:
        if hasattr(instance, "_history_user"):
            return cast(models.Model | None, cast(Any, instance)._history_user)
        from .context import actor_context, current_actor

        source = instance._state.db or router.db_for_write(type(instance), instance=instance)
        context = current_changeset(using=self.destination(type(instance), source))
        if context is None:
            return None
        with actor_context(getattr(context, "_provenance_actor", current_actor())):
            user = context.history_user()
        if user is not None and user._state.db is not None and user._state.db != context._state.db:
            raise ValueError("A persisted history user must share the historical database.")
        return user

    def record_rows(self, model: type[models.Model], count: int, *, using: str) -> None:
        context = current_changeset(using=using)
        if context is not None:

            def committed() -> None:
                label = model._meta.label_lower
                if label not in context.touched:
                    context.touched.append(label)
                context.counts[label] = context.counts.get(label, 0) + count

            transaction.on_commit(committed, using=using)

    def diff(self, first: Any, last: Any, *, previous: Any = _UNLOADED) -> Any:
        """Return the native editable-field delta across a logical boundary.

        A projection can supply its already fetched predecessor. A creation and
        deletion within this boundary has no net field delta.
        """

        model = type(last.history_object)
        key = model._meta.pk.attname
        if previous is _UNLOADED:
            previous = (
                type(last)
                .objects.using(last._state.db)
                .filter(**{key: getattr(last, key)}, history_id__lt=first.history_id)
                .order_by("-history_id")
                .first()
            )
        self.require_complete_state(first, last, previous)
        eligible = {
            field.name
            for field in model._meta.concrete_fields
            if field.editable and not field.primary_key and not (field.remote_field and field.remote_field.parent_link)
        }
        included = [field.name for field in last.tracked_fields if field.name in eligible]
        if last.history_type == "-":
            created_here = first.history_type == "+" and previous is None
            absent_before = previous is not None and previous.history_type == "-"
            if created_here or absent_before:
                return last.diff_against(last, included_fields=[])
            prior = previous if previous is not None else last
            empty = type(last)(**{model._meta.get_field(name).attname: None for name in included})
            return empty.diff_against(prior, included_fields=included)
        prior = previous if previous is not None else type(last)()
        return last.diff_against(prior, included_fields=included)

    def bulk_history_create(
        self,
        model: type[models.Model],
        keys: Iterable[Any],
        history_type: str,
        *,
        using: str,
        history_using: str | None = None,
        instances: Mapping[Any, models.Model] | None = None,
        batch_size: int | None = None,
        default_user: models.Model | None = None,
        default_change_reason: str = "",
        default_date: datetime | None = None,
        custom_historical_attrs: dict[str, Any] | None = None,
    ) -> list[models.Model]:
        if not self.capture_enabled:
            return []
        manager = get_history_manager_for_model(model)
        destination = self.destination(model, using, history_using)
        records = []
        keys = list(dict.fromkeys(keys))
        with write_group() as group:
            for batch in cast(HistoryQuerySet[models.Model], model._base_manager.using(using)).capture_batches(keys):
                rows = list(batch.only(*(field.attname for field in self.fields_included(model))).order_by())
                if not rows:
                    continue
                for row in rows:
                    if instances and row.pk in instances:
                        _metadata(instances[row.pk], row)
                context = self.require_context(destination)
                created = manager.db_manager(destination)._construct_history(
                    rows,
                    batch_size=batch_size,
                    history_type=history_type,
                    default_user=default_user,
                    default_date=default_date,
                    default_change_reason=default_change_reason or (context.change_reason if context else ""),
                    custom_historical_attrs={
                        **(custom_historical_attrs or {}),
                        "history_changeset_id": context.pk if context is not None else None,
                        "history_source_complete": True,
                        **self.source_database_attributes(manager.model, using=using),
                    },
                )
                records.extend(created)
                group.seen.update((model._meta.label_lower, row.pk, history_type) for row in rows)
                for record, row in zip(created, rows, strict=True):
                    self.create_historical_record_m2ms(record, row)
                self.record_rows(model, len(created), using=destination)
            for child in self.capture_children(model):
                owner = model_history(child)
                if owner:
                    link = child._meta.parents[model]
                    assert link is not None
                    children = dict(
                        child._base_manager.using(using)
                        .filter(**{f"{link.attname}__in": keys})
                        .values_list("pk", link.attname)
                    )
                    owner.bulk_history_create(
                        child,
                        children,
                        history_type,
                        using=using,
                        history_using=history_using,
                        instances={
                            key: instances[parent_key]
                            for key, parent_key in children.items()
                            if instances and parent_key in instances
                        },
                        batch_size=batch_size,
                        default_user=default_user,
                        default_change_reason=default_change_reason,
                        default_date=default_date,
                        custom_historical_attrs=custom_historical_attrs,
                    )
        return records

    def create_historical_record(self, instance: models.Model, history_type: str, using: str | None = None) -> None:
        model = type(instance)
        alias = using or instance._state.db or router.db_for_write(model, instance=instance)
        with write_group() as group:
            key = model._meta.label_lower, instance.pk, history_type
            if key in group.seen:
                group.seen.remove(key)
                return
            destination = self.destination(model, alias)
            self.require_context(destination)
            try:
                stored = (
                    model._base_manager.using(alias)
                    .only(*(f.attname for f in self.fields_included(model)))
                    .get(pk=instance.pk)
                )
            except ObjectDoesNotExist:
                if history_type == "-":
                    return
                raise
            _metadata(instance, stored)
            super().create_historical_record(stored, history_type, using=alias)
            self.record_rows(model, 1, using=destination)

    def post_save(self, instance: models.Model, created: bool, using: str | None = None, **kwargs: Any) -> None:
        selected = kwargs.get("update_fields")
        if selected is not None:
            tracked = {
                name
                for field in self.fields_included(type(instance))
                if not getattr(field, "auto_now", False)
                for name in (field.name, field.attname)
            }
            if not tracked.intersection(selected):
                return
        super().post_save(instance, created, using=using, **kwargs)

    def pre_delete(self, instance: models.Model, **kwargs: Any) -> None:
        super().pre_delete(instance, **kwargs)
        if self.capture_enabled and not self.cascade_delete_history:
            self.create_historical_record(instance, "-", using=kwargs.get("using"))

    def post_delete(self, instance: models.Model, using: str | None = None, **kwargs: Any) -> None:
        if self.cascade_delete_history:
            super().post_delete(instance, using=using, **kwargs)

    def get_meta_options(self, model: type[models.Model]) -> dict[str, Any]:
        return {**super().get_meta_options(model), "app_label": model._meta.app_label}

    def fields_included(self, model: type[models.Model]) -> list[Any]:
        return [
            field
            for field in super().fields_included(model)
            if (field.concrete or field.is_relation or field.auto_created)
            and not isinstance(field, models.GeneratedField)
        ]

    def copy_fields(self, model: type[models.Model]) -> dict[str, Any]:
        copied = super().copy_fields(model)
        for name, field in copied.items():
            source = model._meta.get_field(name)
            if isinstance(source, models.ForeignKey) and source.auto_created and source.remote_field.parent_link:
                field.auto_created = False
                field.remote_field.parent_link = False
        return cast(dict[str, Any], copied)

    def capture_children(self, model: type[models.Model]) -> tuple[type[models.Model], ...]:
        return tuple(
            sorted(
                (
                    relation.related_model
                    for relation in model._meta.related_objects
                    if relation.parent_link
                    and relation.related_model._meta.managed
                    and tuple(relation.related_model._meta.parents) == (model,)
                ),
                key=lambda child: child._meta.label_lower,
            )
        )

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        return {
            **super().get_extra_fields(model, fields),
            "_provenance_history": classproperty(lambda cls: self),
        }


_CaptureManagerBase = models.Manager.from_queryset(HistoryQuerySet)


class CaptureManager[T: models.Model](_CaptureManagerBase[T]):  # pyright: ignore[reportInvalidTypeArguments]
    """Manager preserving capture for queryset mutations."""


class CaptureHistoryMixin(models.Model):
    """Capture writes without imposing a host's replay/removal policy."""

    history = ModelHistory(inherit=True)
    if TYPE_CHECKING:
        save_without_historical_record = models.Model.save
    objects: ClassVar[models.Manager[Self]] = CaptureManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        abstract = True
        base_manager_name = "objects"

    def save_base(
        self,
        raw: bool = False,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        alias = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=alias, savepoint=not raw), write_group() as group:
            owner = model_history(type(self))
            if not raw and owner and owner.capture_enabled and not hasattr(self, "skip_history_when_saving"):
                owner.require_context(owner.destination(type(self), alias))
            super().save_base(raw, force_insert, force_update, alias, update_fields)
            if (
                self._meta.proxy
                and not raw
                and owner
                and owner.capture_enabled
                and not hasattr(self, "skip_history_when_saving")
            ):
                concrete = (self._meta.concrete_model or type(self))._base_manager.using(alias).get(pk=self.pk)
                _metadata(self, concrete)
                owner.create_historical_record(
                    concrete, "~" if group.saved[concrete._meta.label_lower] else "+", using=alias
                )
            if not raw and not self._meta.proxy and not hasattr(self, "skip_history_when_saving"):
                for parent in self._meta.get_parent_list():
                    parent_owner = model_history(parent)
                    if parent_owner and parent_owner.capture_enabled:
                        stored = parent._base_manager.using(alias).get(pk=self._get_pk_val(parent._meta))
                        _metadata(self, stored)
                        parent_owner.create_historical_record(
                            stored,
                            "~" if group.saved[parent._meta.label_lower] else "+",
                            using=alias,
                        )

    def _save_table(
        self,
        raw: bool = False,
        cls: type[models.Model] | None = None,
        force_insert: bool = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> bool:
        # Django owns insert-versus-update, including explicit existing primary keys.
        updated = cast(
            bool,
            cast(Any, super())._save_table(raw, cls, force_insert, force_update, using, update_fields),
        )
        with write_group() as group:
            group.saved[(cls or type(self))._meta.label_lower] = updated
        return updated

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        with write_group():
            return super().delete(*args, **kwargs)


class ReplayHistoryMixin(CaptureHistoryMixin, HistoryReplayMixin):
    """Default standalone composition of capture and native replay policy."""

    class Meta(CaptureHistoryMixin.Meta, HistoryReplayMixin.Meta):
        abstract = True


# Historical Angee import name for the canonical capture owner.
_ChangesetHistory = HistoryAttribution
HistoryMixin = CaptureHistoryMixin
