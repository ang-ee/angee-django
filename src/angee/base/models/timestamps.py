"""Timestamp fields and the native write-stamping contract they declare."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from django.db import models
from django.db.models.expressions import Col
from django.utils import timezone
from django.utils.functional import cached_property

# The instance attributes holding a time ``TimestampMixin.stamp_created_at`` stated:
# until the next save or bulk insert takes it, and while that save runs (Django calls
# ``pre_save`` more than once for one insert). ``TimestampMixin.__getstate__`` leaves
# both out of a copy.
_STATED_CREATED_AT = "_stated_created_at"
_SAVING_CREATED_AT = "_saving_created_at"


class CreatedAtField(models.DateTimeField):
    """When a row came into being: Django's ``auto_now_add`` stamp, which a creating owner may state for its insert.

    An insert stamps now, as ``auto_now_add`` does, whatever the attribute
    holds: a row cloned by clearing its primary key, one built from another
    row's values and one inserted again after a rolled-back attempt each get the
    moment of their own insert. The one way to keep an earlier time is
    :meth:`TimestampMixin.stamp_created_at`, which a creating owner calls on the
    unsaved row it builds from a source's record: the next insert of that
    instance keeps the time, a future one clamped to the insert. A stated time
    is kept once, at the insert it was stated for: the save or bulk insert that
    makes it takes it, whether or not it succeeds, so a later insert of the
    instance stamps now and a verb that retries a failed insert states the time
    again. A save holds the time for each of Django's ``pre_save`` calls of its
    insert (one finds expressions, one builds the statement), and a multi-table
    child's save keeps it on the parent table that holds the column; a bulk
    insert calls :meth:`pre_save` once per row and takes each row's own. Only
    those two keep it: an insert made by calling ``save_base`` directly, without
    ``save``, gives the stated time to the call that finds expressions and stamps
    the statement with now, so the row and the instance both hold now.
    Fixtures loaded raw keep the values they carry, as Django always did: a raw
    save never calls :meth:`pre_save`. A save of an existing row stamps nothing.
    History's ``+`` snapshot reads the stored row, so it carries the stated time,
    while its ``history_date`` stays the moment the row was recorded.

    Who may state a time: a creating owner, for a row no reader treats as having
    arrived here when it was stamped. One reader does today: an events send guard
    refuses a message due before its registration or order existed, so a
    registration or an order states none until its owner reads an arrival stamp of
    its own; an owner that then restates the stamp of such a row sets the arrival
    stamp from the old ``created_at`` first, in the same statement. Handles,
    statements and record links keep the moment they were recorded.

    An owner may also move its stamp earlier after the insert, through one writer
    of its own: a party's is the earliest its provenance states
    (``Party.backdate_creation``), so a merge's survivor takes the earliest of
    what it folds in and a batch merge's "oldest" is the source-oldest. A stamp
    that starts, or is moved, into the past never enters a "created since" window
    or a cursor ordered by it: a reader that means "arrived here since" reads an
    arrival fact, never this stamp. None reads a party's, a person's, an
    organization's, a handle's or a knowledge page's that way today (searched:
    ``created_at__gt``, ``__gte``, ``__lt``, ``__lte``, ``__range`` and the date
    lookups, and orderings by ``created_at`` under a keyset cursor, a slice,
    ``first()``/``last()`` or a limit, in the framework, its product, bridge and
    consumer addons, and the web layer); lists sort and group by it as a record's
    age.

    It deconstructs as ``django.db.models.DateTimeField``: it adds no persisted
    option, so a host's migration state and historical models hold the field Django
    declares, and no host generates a migration for it. Its column is typed as that
    field too (:attr:`value_field`, as Django's ``GeneratedField`` types its column by
    its ``output_field``), so an expression combining it with any other
    ``DateTimeField`` (``Coalesce("sent_at", "created_at")``) resolves its type as
    before.
    """

    @cached_property
    def value_field(self) -> models.DateTimeField:
        """The field Django declares, which types this column in every expression."""

        return models.DateTimeField()

    @cached_property
    def cached_col(self) -> Col:
        return Col(self.model._meta.db_table, self, self.value_field)

    def get_col(self, alias: str, output_field: Any = None) -> Col:
        if output_field is None or output_field == self:
            if alias == self.model._meta.db_table:
                return self.cached_col
            output_field = self.value_field
        return super().get_col(alias, output_field)

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        if add and self.auto_now_add:
            held = model_instance.__dict__
            # A save holds its stated time for each of its calls; a bulk insert calls once per row.
            stated = held[_SAVING_CREATED_AT] if _SAVING_CREATED_AT in held else held.pop(_STATED_CREATED_AT, None)
            if stated is not None:
                value = min(stated, timezone.now())
                setattr(model_instance, self.attname, value)
                return value
        return super().pre_save(model_instance, add)

    def deconstruct(self) -> Any:
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.DateTimeField", args, kwargs


class TimestampMixin(models.Model):
    """Add conventional creation and update timestamps to a model."""

    created_at = CreatedAtField(auto_now_add=True, db_index=True)
    """When the row came into being: its insert, or the time its creator stated (:meth:`stamp_created_at`)."""

    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    """The timestamp when the row was most recently saved."""

    class Meta:
        """Django model options for timestamp-only abstract inheritance."""

        abstract = True

    def stamp_created_at(self, at: datetime | None) -> Self:
        """State when this unsaved row came into being, for its insert to keep as ``created_at``; return the row.

        The one way a creating owner states a source's creation time
        (:class:`CreatedAtField`). The next save or bulk insert of this instance
        takes ``at``, kept by its insert, a future one clamped to the insert: a
        stated time is kept once, at the insert it was stated for, and a save
        that fails takes it too. Every other insert stamps now, so a verb that
        retries a failed insert states the time again, and a pickled or
        ``copy``-ed row carries no stated time. ``None`` states none: the insert
        stamps now. A saved row is refused: its creation time moves only through
        its owner's own writer.
        """

        if not self._state.adding:
            raise ValueError("A creation time is stated for a row's insert, before the row is saved.")
        if at is None:
            self.__dict__.pop(_STATED_CREATED_AT, None)
        else:
            self.__dict__[_STATED_CREATED_AT] = at
        return self

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the row; its insert keeps a time :meth:`stamp_created_at` stated, which this save takes either way."""

        stated = self.__dict__.pop(_STATED_CREATED_AT, None)
        if stated is None:
            super().save(*args, **kwargs)
            return
        self.__dict__[_SAVING_CREATED_AT] = stated
        try:
            super().save(*args, **kwargs)
        finally:
            del self.__dict__[_SAVING_CREATED_AT]

    def __getstate__(self) -> dict[str, Any]:
        """Leave a stated creation time out of a pickled or copied row: a copy stamps its own insert."""

        state = super().__getstate__()
        state.pop(_STATED_CREATED_AT, None)
        state.pop(_SAVING_CREATED_AT, None)
        return state


def update_fields_with_auto_now(instance: models.Model, update_fields: Any) -> set[str]:
    """Return non-empty ``update_fields`` plus this model's ``auto_now`` fields."""

    fields = set(update_fields)
    if not fields:
        return fields
    return fields | set(_auto_now_fields(type(instance)))


def auto_now_stamps(model: type[models.Model], now: datetime) -> dict[str, Any]:
    """Return the ``auto_now`` columns a queryset update of ``model`` must stamp itself."""

    return dict.fromkeys(_auto_now_fields(model), now)


def _auto_now_fields(model: type[models.Model]) -> tuple[str, ...]:
    """Return the names of ``model``'s ``auto_now`` fields, which every write stamps."""

    # Django temporal fields declare auto_now; other Field subclasses do not.
    return tuple(field.name for field in model._meta.concrete_fields if getattr(field, "auto_now", False))
