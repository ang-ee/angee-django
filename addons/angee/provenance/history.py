"""Native Django history capture composed through django-simple-history."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db import models, router, transaction
from django.db.models.base import ModelBase
from django.utils import timezone
from django.utils.functional import classproperty
from simple_history.manager import HistoryManager
from simple_history.models import HistoricalRecords
from simple_history.signals import pre_create_historical_record
from simple_history.utils import get_change_reason_from_object, get_history_manager_for_model

from angee.provenance.context import changeset_model_label, current_changeset, write_group
from angee.provenance.querysets import HistoryQuerySet, model_history

_UNLOADED = object()


def _copy_history_metadata(source: models.Model, target: models.Model) -> None:
    """Keep native history overrides while loading authoritative stored fields."""

    for name in ("_history_date", "_history_user", "_change_reason"):
        if hasattr(source, name):
            setattr(target, name, getattr(source, name))


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
        """Capture stored rows through the native bulk-history manager contract."""

        objects = list(objs)
        if not objects:
            return []
        if any(obj.pk is None for obj in objects):
            raise ValueError("History capture requires a primary key for every stored row.")
        history = self.model._provenance_history
        by_database: dict[str, list[models.Model]] = {}
        for obj in objects:
            alias = obj._state.db or router.db_for_write(self.model.instance_type, instance=obj)
            by_database.setdefault(alias, []).append(obj)
        captured = []
        for alias, sources in by_database.items():
            captured.extend(
                history.bulk_history_create(
                    self.model.instance_type,
                    [obj.pk for obj in sources],
                    history_type or ("~" if update else "+"),
                    using=alias,
                    history_using=self._db,
                    instances={obj.pk: obj for obj in sources},
                    batch_size=batch_size,
                    default_user=default_user,
                    default_change_reason=default_change_reason,
                    default_date=default_date,
                    custom_historical_attrs=custom_historical_attrs,
                )
            )
        return captured

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
        """Fill upstream 3.13's constructor-loop gap for explicit deletion history types."""

        if not getattr(settings, "SIMPLE_HISTORY_ENABLED", True):
            return []
        rows = []
        for instance in objs:
            attrs = {field.attname: getattr(instance, field.attname) for field in self.model.tracked_fields}
            row = self.model(
                history_date=getattr(instance, "_history_date", default_date or timezone.now()),
                history_user=getattr(
                    instance, "_history_user", default_user or self.model.get_default_history_user(instance)
                ),
                history_change_reason=get_change_reason_from_object(instance) or default_change_reason,
                history_type=history_type or ("~" if update else "+"),
                **attrs,
                **(custom_historical_attrs or {}),
            )
            if hasattr(self.model, "history_relation"):
                row.history_relation_id = instance.pk
            rows.append(row)
        return cast(list[models.Model], self.model.objects.using(self.db).bulk_create(rows, batch_size=batch_size))


class ModelHistory(HistoricalRecords):
    """Capture concrete descendants and full stored snapshots through native hooks."""

    @property
    def capture_enabled(self) -> bool:
        """Honor the native simple-history switch at the registered history owner."""

        return bool(getattr(settings, "SIMPLE_HISTORY_ENABLED", True))

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
        selected = (("base", sender._base_manager), ("default", sender._default_manager))
        checked: set[int] = set()
        for role, manager in selected:
            if id(manager) in checked:
                continue
            checked.add(id(manager))
            if not isinstance(manager.get_queryset(), HistoryQuerySet):
                raise ImproperlyConfigured(
                    f"{sender._meta.label}: ModelHistory requires a history-capable {role} manager. "
                    "Declare a manager composing HistoryQuerySet and select it through the native Meta manager options."
                )
        super().finalize(sender, **kwargs)
        pre_create_historical_record.connect(
            self.set_changeset,
            sender=get_history_manager_for_model(sender).model,
            weak=False,
        )

    def set_changeset(self, instance: models.Model, history_instance: _ChangesetHistory, **kwargs: Any) -> None:
        """Bind attribution after native construction and before the history save."""

        history_instance.changeset_id = current_changeset(required=True, record=True).pk

    def get_change_reason_for_object(self, instance: models.Model, history_type: str, using: str | None) -> str:
        """Honor a native instance override before the logical boundary's reason."""

        context = current_changeset()
        return get_change_reason_from_object(instance) or (context.change_reason if context is not None else "")

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
                type(last)
                .objects.using(last._state.db)
                .filter(**{key: getattr(last, key)}, history_id__lt=first.history_id)
                .order_by("-history_id")
                .first()
            )
        fields = {
            field.name: field
            for field in model._meta.concrete_fields
            if field.editable and not field.primary_key and not (field.remote_field and field.remote_field.parent_link)
        }
        included = [field.name for field in last.tracked_fields if field.name in fields]
        deleted = last.history_type == "-"
        if deleted and (
            first.history_type == "+" and previous is None or previous is not None and previous.history_type == "-"
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
        """Batch stored snapshots of this model and its tracked MTI descendants.

        ``using`` selects the stored source rows. An explicit ``history_using``
        binds the historical destination; otherwise its native routing policy
        applies. ``instances`` supplies only native history metadata overrides.
        Each batch records pending receiver keys, consumed by native receivers.
        """

        if not self.capture_enabled:
            return []
        captured: list[models.Model] = []
        manager = get_history_manager_for_model(model)
        with write_group() as group:
            keys = list(dict.fromkeys(keys))
            names = [field.attname for field in self.fields_included(model)]
            destination = history_using or (using if self.use_base_model_db else router.db_for_write(manager.model))
            for rows in cast(HistoryQuerySet, model._base_manager.using(using)).capture_batches(keys):
                snapshots = list(rows.only(*names).order_by())
                if not snapshots:
                    continue
                for snapshot in snapshots:
                    if instances is not None and snapshot.pk in instances:
                        _copy_history_metadata(instances[snapshot.pk], snapshot)
                context = current_changeset(required=True, record=True)
                records = manager.db_manager(destination)._construct_history(
                    snapshots,
                    batch_size=batch_size,
                    history_type=history_type,
                    default_user=default_user,
                    default_change_reason=default_change_reason
                    or (context.change_reason if context is not None else ""),
                    default_date=default_date,
                    custom_historical_attrs={**(custom_historical_attrs or {}), "changeset": context},
                )
                captured.extend(records)
                group.seen.update((model._meta.label_lower, snapshot.pk, history_type) for snapshot in snapshots)
                for record, snapshot in zip(records, snapshots, strict=True):
                    self.create_historical_record_m2ms(record, snapshot)
                self.record_rows(model, len(records), using=destination)
            for child in self.capture_children(model):
                history = model_history(child)
                if history is not None:
                    history.bulk_history_create(
                        child,
                        keys,
                        history_type,
                        using=using,
                        history_using=history_using,
                        instances=instances,
                        batch_size=batch_size,
                        default_user=default_user,
                        default_change_reason=default_change_reason,
                        default_date=default_date,
                        custom_historical_attrs=custom_historical_attrs,
                    )
        return captured

    def create_historical_record(self, instance: models.Model, history_type: str, using: str | None = None) -> None:
        """Keep native signals and M2M capture on the same stored-snapshot writer."""

        model = type(instance)
        key = (model._meta.label_lower, instance.pk, history_type)
        with write_group() as group:
            if key in group.seen:
                group.seen.remove(key)
                return
            current_changeset(required=True, record=True)
            alias = using or instance._state.db or router.db_for_write(model, instance=instance)
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
            _copy_history_metadata(instance, snapshot)
            super().create_historical_record(snapshot, history_type, using=alias)
            history_manager = get_history_manager_for_model(model)
            history_using = alias if self.use_base_model_db else router.db_for_write(history_manager.model)
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

    def capture_children(self, model: type[models.Model]) -> tuple[type[models.Model], ...]:
        """Follow Django's native reverse parent links for directly inherited tables."""

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

    def get_history_user(self, instance: models.Model) -> models.Model | None:
        """Honor a native instance override before attribution storage's user."""

        if hasattr(instance, "_history_user"):
            return instance._history_user
        context = current_changeset()
        return context.history_user() if context is not None else None

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        extra = super().get_extra_fields(model, fields)
        extra["_provenance_history"] = classproperty(lambda cls: self)
        if model._meta.apps is not apps:
            if self.user_id_field is None and self.user_model is None:
                user = get_user_model()
                extra["history_user"].remote_field.model = user
                extra["history_user"].remote_field.field_name = user._meta.pk.name
            field = _ChangesetHistory._meta.get_field("changeset").clone()
            field.remote_field.model = apps.get_model(changeset_model_label())
            field.remote_field.field_name = field.remote_field.model._meta.pk.name
            extra["changeset"] = field
        return extra


_CaptureManagerBase = models.Manager.from_queryset(HistoryQuerySet)


class CaptureManager[T: models.Model](_CaptureManagerBase[T]):
    """Named native manager contract for models opting into captured querysets."""

    model: type[T]


class HistoryMixin(models.Model):
    """Opt a Django model into instance, collector, and queryset history capture."""

    history = ModelHistory(inherit=True)
    objects: ClassVar[models.Manager[Self]] = CaptureManager()
    _base_manager: ClassVar[models.Manager[Self]]
    _default_manager: ClassVar[models.Manager[Self]]
    if TYPE_CHECKING:
        save_without_historical_record = models.Model.save

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
        """Validate attribution before SQL and scope native save dispatch to one group."""

        alias = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=alias, savepoint=not raw), write_group():
            if not raw and not hasattr(self, "skip_history_when_saving"):
                history = model_history(type(self))
                if history is not None and history.capture_enabled:
                    current_changeset(required=True, record=True)
            super().save_base(raw, force_insert, force_update, alias, update_fields)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Keep instance and collector receivers in one ephemeral write group."""

        history = model_history(type(self))
        if history is not None and history.capture_enabled and not history.cascade_delete_history:
            current_changeset(required=True)
        with write_group():
            return super().delete(*args, **kwargs)
