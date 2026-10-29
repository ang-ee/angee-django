"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar, Self, TypeVar, cast

from angee.base.serialization import strip_null_bytes
from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.utils import timezone
from rebac import (
    RebacMixin,
    current_actor,
)

from angee.base.models.persistence import PersistenceMixin
from angee.base.models.timestamps import auto_now_stamps, update_fields_with_auto_now
from angee.base.permissions import actor_user_id, instance_actor
from angee.base.querysets import CanonicalQuerySet, by_key
from angee.base.signals import bulk_updated

_ModelT = TypeVar("_ModelT", bound=models.Model)
_ArchiveModelT = TypeVar("_ArchiveModelT", bound=models.Model)
_TrashModelT = TypeVar("_TrashModelT", bound=models.Model)

ARCHIVE_FLAG_FIELD = "is_archived"
TRASH_FLAG_FIELD = "is_trashed"

TRASH_REASON_MAX_LENGTH = 1000
"""Upper bound, in characters, of the optional reason recorded with a trash."""



class ArchiveMixin(models.Model):
    """Add a soft-archive flag to a model.

    One vocabulary, everywhere: the column is ``is_archived`` (see
    :data:`ARCHIVE_FLAG_FIELD`) and the read scopes are ``.archived()`` /
    ``.unarchived()`` (compose :class:`ArchiveQuerySet` into the model's
    queryset). Archived rows are soft-hidden from default surfaces but kept for
    an explicit archived facet — a metadata fact the field classifier carries as
    ``archivable``, not per-page logic. This is archival, distinct from a
    soft-delete/trash flag or an enablement flag, which own different contracts.
    """

    is_archived = models.BooleanField(default=False, db_index=True)
    """Whether the row is archived — soft-hidden from default pickers and lists."""

    class Meta:
        """Django model options for archive-only abstract inheritance."""

        abstract = True


class ArchiveQuerySet(models.QuerySet[_ArchiveModelT]):
    """Composable read scopes for the :class:`ArchiveMixin` archive flag.

    Mix into a model's queryset alongside its base queryset (e.g.
    ``class DriveQuerySet(ArchiveQuerySet[Drive], AngeeQuerySet[Drive])``) so the
    archive vocabulary — ``.archived()`` / ``.unarchived()`` — reads as chainable
    predicates over the one ``is_archived`` column rather than repeated inline
    filters.
    """

    def archived(self) -> Self:
        """Return rows flagged archived."""

        return cast(Self, self.filter(**{ARCHIVE_FLAG_FIELD: True}))

    def unarchived(self) -> Self:
        """Return rows not flagged archived — the default picker/list scope."""

        return cast(Self, self.filter(**{ARCHIVE_FLAG_FIELD: False}))


def clean_trash_reason(reason: Any) -> str:
    """Return the stored form of an optional trash reason, refusing an oversized one."""

    text = strip_null_bytes(str(reason or "")).strip()
    if len(text) > TRASH_REASON_MAX_LENGTH:
        raise ValidationError({"reason": f"Keep the reason within {TRASH_REASON_MAX_LENGTH} characters."})
    return text


class TrashMixin(PersistenceMixin, models.Model):
    """Soft-remove ("trash") a row while keeping it, with who, when and why.

    Trash is removal, distinct from archival (:class:`ArchiveMixin`): nothing is
    destroyed, but the row leaves every surface for readers who do not manage
    it. The owning model's Zed declares that audience — a ``withheld`` arm over a
    constant relation filtered on ``is_trashed`` (see ``knowledge/page``) — so
    lists, counts and search lose the row inside the permission engine. Managers
    act through the row's ``delete`` permission: the shared ``trash_record`` /
    ``restore_record`` GraphQL verbs check it before calling :meth:`trash` /
    :meth:`restore`; a domain verb with its own authority (record-chatter
    moderation) checks that authority instead.

    Both verbs refuse a no-op state change under a row lock and persist through
    ``save(update_fields=...)``, so REBAC write gates and history tracking still
    apply. Restoring clears the stamps (a history-tracked model keeps them in its
    history) and re-grants nothing: the row is readable by whoever its
    permissions admit today. Compose :class:`TrashQuerySet` into the model's
    queryset for the ``.trashed()`` / ``.untrashed()`` scopes.
    """

    TRASH_FIELDS: ClassVar[tuple[str, ...]] = (TRASH_FLAG_FIELD, "trashed_at", "trashed_by", "trash_reason")
    """Every column a trash or restore writes."""

    UNTRASHED_VALUES: ClassVar[Mapping[str, Any]] = MappingProxyType(
        {
            TRASH_FLAG_FIELD: False,
            "trashed_at": None,
            "trashed_by": None,
            "trash_reason": "",
        }
    )
    """Column values of a row outside the trash, for owners restoring in a wider write."""

    is_trashed = models.BooleanField(default=False, db_index=True, editable=False)
    """Whether the row is in the trash — withheld from everyone who does not manage it."""

    trashed_at = models.DateTimeField(null=True, blank=True, editable=False)
    """When the row was moved to the trash."""

    trashed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    """The user who moved the row to the trash, when a user acted."""

    trash_reason = models.TextField(blank=True, default="", editable=False)
    """The optional reason recorded with the trash, at most ``TRASH_REASON_MAX_LENGTH`` characters."""

    class Meta:
        """Django model options for trash-only abstract inheritance."""

        abstract = True

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Require the default manager to carry the trash row scopes."""

        errors = super().check(**kwargs)
        if (
            not cls._meta.abstract
            and cls.stores_trash()
            and not isinstance(cls._default_manager.get_queryset(), TrashQuerySet)
        ):
            errors.append(
                checks.Error(
                    f"{cls._meta.label}'s default manager must compose TrashQuerySet.",
                    hint="Declare a default manager whose queryset inherits TrashQuerySet.",
                    obj=cls,
                    id="angee.E036",
                )
            )
        return errors

    @classmethod
    def stores_trash(cls) -> bool:
        """Return whether this concrete model stores its own trash flag.

        A multi-table child inherits its parent's flag; the parent owns trash
        for both, so shared verbs address the parent only.
        """

        return cls._meta.get_field(TRASH_FLAG_FIELD).model is cls

    def trash(self, *, reason: str = "") -> None:
        """Move this row to the trash, stamping the acting user, time and reason."""

        reason = clean_trash_reason(reason)
        db = self._state.db
        with transaction.atomic(using=db):
            self._require_trash_state(trashed=False)
            self.validate_trash()
            self.is_trashed = True
            self.trashed_at = timezone.now()
            self.trashed_by_id = actor_user_id(instance_actor(self))
            self.trash_reason = reason
            self.save(using=db, update_fields=update_fields_with_auto_now(self, self.TRASH_FIELDS))

    def validate_trash(self) -> None:
        """Refuse domain-invalid removal while the shared trash owner holds its row lock."""

    def restore(self) -> None:
        """Take this row out of the trash, clearing its trash stamps."""

        db = self._state.db
        with transaction.atomic(using=db):
            self._require_trash_state(trashed=True)
            for name, value in self.UNTRASHED_VALUES.items():
                setattr(self, name, value)
            self.save(using=db, update_fields=update_fields_with_auto_now(self, self.TRASH_FIELDS))

    def revert_removal(self, previous: Any = None, *, reason: str = "") -> None:
        """Replay removal through the trash owner shared by MTI descendants."""

        owner = self._meta.get_field(TRASH_FLAG_FIELD).model
        if not issubclass(owner, TrashMixin):
            raise ImproperlyConfigured("The trash flag must be declared by TrashMixin.")
        target = owner._base_manager.db_manager(hints={"instance": self}).get(pk=self.pk)
        if (actor := instance_actor(self)) is not None:
            if not isinstance(target, RebacMixin):
                raise ImproperlyConfigured("Actor-attributed trash replay requires RebacMixin.")
            target.with_actor(actor)
        trashed = previous is None or previous.history_type == "-" or previous.is_trashed
        if target.is_trashed != trashed:
            if trashed:
                target.trash(
                    reason=previous.trash_reason if previous is not None and previous.history_type != "-" else reason,
                )
            else:
                target.restore()

    def _require_trash_state(self, *, trashed: bool) -> None:
        """Lock the stored row and refuse a trash verb that would change nothing."""

        owner = self._meta.get_field(TRASH_FLAG_FIELD).model
        if not issubclass(owner, TrashMixin):
            raise ImproperlyConfigured("The trash flag must be declared by TrashMixin.")
        stored = (
            owner.system_queryset(lock=("self",))
            .using(owner._base_manager.db_manager(hints={"instance": self}).db)
            .filter(pk=self.pk)
            .values_list(TRASH_FLAG_FIELD, flat=True)
            .first()
        )
        if stored is None:
            raise ValidationError(f"This {self._meta.verbose_name} no longer exists.")
        if bool(stored) is not trashed:
            state = "is not in" if trashed else "is already in"
            raise ValidationError({TRASH_FLAG_FIELD: f"This {self._meta.verbose_name} {state} the trash."})


class TrashQuerySet(CanonicalQuerySet[_TrashModelT]):
    """Composable row scopes for the :class:`TrashMixin` trash flag.

    Mix into the model's queryset beside its base queryset (e.g.
    ``class PageQuerySet(TrashQuerySet[Page], AngeeQuerySet[Page])``).
    """

    def trashed(self) -> Self:
        """Return rows in the trash — the removed-records list's scope."""

        return cast(Self, self.filter(**{TRASH_FLAG_FIELD: True}))

    def untrashed(self) -> Self:
        """Return rows outside the trash — the default surface's scope."""

        return cast(Self, self.filter(**{TRASH_FLAG_FIELD: False}))

    def canonical(self) -> Self:
        """Operational rows are outside the trash; the merge scope composes on top.

        The chain ends at the base queryset every model composes, so a trashable
        mergeable model's ``canonical()`` reads the same on every model.
        """

        return super().canonical().untrashed()

    def trash_targets(self) -> Self:
        """Return the rows the shared trash verbs may address.

        Override to keep rows whose removal another verb owns out of the shared
        surface (record-chatter messages are moderated through their record).
        """

        return self

    def trash(self, *, reason: str = "", at: datetime | None = None) -> int:
        """Bulk-trash this set's untrashed rows; the caller has authorized every row.

        Protected API for an owning verb (an external sync retiring vanished
        rows, a folder taking its contents along): a queryset update emits no
        instance-save signals; the composed queryset owns history capture.
        ``at`` stamps rows trashed together.
        """

        now = timezone.now()
        return self.untrashed()._update_trash({
            **auto_now_stamps(self.model, now),
            "is_trashed": True,
            "trashed_at": at or now,
            "trashed_by_id": actor_user_id(current_actor()),
            "trash_reason": clean_trash_reason(reason),
        })

    def restore(self) -> int:
        """Bulk-restore this set's trashed rows; the caller has authorized every row.

        The protected counterpart of :meth:`trash`, with the same contract.
        """

        return self.trashed()._update_trash({
            **auto_now_stamps(self.model, timezone.now()), **TrashMixin.UNTRASHED_VALUES,
        })

    def _update_trash(self, values: Mapping[str, Any]) -> int:
        """Freeze authorized keys, update through history, then publish bounded batches."""

        with transaction.atomic(using=self.db):
            keys = list(self.select_for_update().values_list("pk", flat=True))
            if not keys:
                return 0
            changed = sum(batch.update(**values) for batch in by_key(self, keys))
            if bulk_updated.has_listeners(self.model):
                for batch in by_key(self.model._base_manager.using(self.db), keys):
                    bulk_updated.send_robust(
                        sender=self.model, instances=tuple(batch), update_fields=frozenset(values), using=self.db,
                    )
            return changed
