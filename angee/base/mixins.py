"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import copy
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Self, TypeVar, cast

import reversion
from django.conf import settings
from django.core import checks
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, models, router, transaction
from django.db.models import F, Value
from django.db.models.functions import Replace
from django.utils import timezone
from rebac import PermissionDenied, current_actor, system_context
from rebac.actors import is_sudo
from rebac.field_visibility import gated_read_fields
from rebac.managers import RebacQuerySet, TrackedQuerySet
from simple_history.models import HistoricalRecords

from angee.base.actors import actor_user_id, instance_actor
from angee.base.errors import DomainError
from angee.base.fields import SqidField
from angee.base.indexes import PatternOpsIndex
from angee.base.querysets import _AngeeQuerySetMixin
from angee.base.scoping import system_queryset
from angee.base.serialization import canonical_json_sha256, json_safe, strip_null_bytes

_ModelT = TypeVar("_ModelT", bound=models.Model)
_ArchiveModelT = TypeVar("_ArchiveModelT", bound=models.Model)
_TrashModelT = TypeVar("_TrashModelT", bound=models.Model)
_HierarchyModelT = TypeVar("_HierarchyModelT", bound="HierarchyMixin")

ARCHIVE_FLAG_FIELD = "is_archived"
"""The one archive-flag column name — the single archive vocabulary word.

Every model that composes :class:`ArchiveMixin` carries this exact column, and
the resource-metadata field classifier recognises the archive flag by this name
(``angee.data.field_classification.is_archive_field``). Keeping the name
identical everywhere is the contract that lets pickers default-filter archived
rows and lists expose an archived facet without per-model wiring.
"""

TRASH_FLAG_FIELD = "is_trashed"
"""The one trash-flag column name — the single trash vocabulary word.

Every model that composes :class:`TrashMixin` carries this exact column, and the
resource-metadata field classifier recognises it by this name
(``angee.data.field_classification.is_trash_field``) so shared views can offer a
removed-records list for any trashable resource without per-model wiring.
"""

TRASH_REASON_MAX_LENGTH = 1000
"""Upper bound, in characters, of the optional reason recorded with a trash."""


def retained_set_null(collector: Any, field: Any, sub_objs: Iterable[models.Model], using: str) -> None:
    """Null a retained reference through Django's materialized collector path.

    Django's unevaluated SET_NULL path calls QuerySet.update(), which append-only
    querysets reject. Materializing here selects the collector's native
    UpdateQuery.update_batch path without opening a general update escape hatch.
    This loads all matching referencing rows into memory for each FK being
    nullified; deleting a heavily referenced target can require substantial memory.
    AuditMixin and retained domain references share this collector policy.
    """

    del using
    collector.add_field_update(field, None, list(sub_objs))


audit_set_null = retained_set_null
"""Historical import for released migration bodies."""


class TimestampMixin(models.Model):
    """Add conventional creation and update timestamps to a model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    """The timestamp when the row was first created."""

    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    """The timestamp when the row was most recently saved."""

    class Meta:
        """Django model options for timestamp-only abstract inheritance."""

        abstract = True


def update_fields_with_auto_now(instance: models.Model, update_fields: Any) -> set[str]:
    """Return non-empty ``update_fields`` plus this model's ``auto_now`` fields."""

    fields = set(update_fields)
    if not fields:
        return fields
    return fields | {field.name for field in instance._meta.fields if getattr(field, "auto_now", False)}


def auto_now_stamps(model: type[models.Model], now: datetime) -> dict[str, Any]:
    """Return the ``auto_now`` columns a queryset update of ``model`` must stamp itself."""

    return {field.name: now for field in model._meta.concrete_fields if getattr(field, "auto_now", False)}


class SqidMixin(models.Model):
    """Add an opaque public identifier backed by the model primary key.

    A model sets only the varying fact — its prefix — as ``sqid_prefix``
    (e.g. ``sqid_prefix = "nte_"``); the shared ``sqid`` column reads it (see
    ``SqidField.contribute_to_class``), so no model re-declares the field.
    """

    sqid_prefix: ClassVar[str] = ""
    """Public-id prefix for ``sqid`` (e.g. ``"nte_"``); empty means no prefix."""

    sqid = SqidField(real_field_name="id", min_length=8)
    """Opaque public identifier encoded from the integer primary key."""

    class Meta:
        """Django model options for sqid-only abstract inheritance."""

        abstract = True

    def public_id_value(self) -> Any:
        """Return the raw public identifier value for this instance."""

        return self.sqid

    @classmethod
    def public_id_lookup(cls, value: str) -> dict[str, Any]:
        """Return the Django lookup for this model's public identifier."""

        return {"sqid": value}

    @classmethod
    def public_id_from_pk(cls, value: Any) -> str:
        """Return the public id encoded from this model's primary-key value."""

        # SqidMixin declares ``sqid = SqidField(...)`` unconditionally, so the column
        # is always a SqidField on any subclass.
        field = cast(SqidField, cls._meta.get_field("sqid"))
        return field.public_id_from_value(value)


class AuditMixin(models.Model):
    """Add conventional user-owned audit foreign keys to a model."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=retained_set_null,
        related_name="+",
    )
    """The user that created the row, when known."""

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=retained_set_null,
        related_name="+",
    )
    """The user that most recently updated the row, when known."""

    class Meta:
        """Django model options for audit-only abstract inheritance."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row after stamping user audit fields."""

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if not update_fields:
                super().save(*args, **kwargs)
                return

        user_id = actor_user_id(instance_actor(self))
        touched: set[str] = set()
        if user_id is not None:
            if self._state.adding:
                if getattr(self, "created_by_id", None) is None:
                    self.created_by_id = user_id
                    touched.add("created_by")
                if getattr(self, "updated_by_id", None) is None:
                    self.updated_by_id = user_id
                    touched.add("updated_by")
            else:
                self.updated_by_id = user_id
                touched.add("updated_by")

        if update_fields is not None:
            kwargs["update_fields"] = update_fields_with_auto_now(self, update_fields | touched)
        super().save(*args, **kwargs)


class OwnerQuerySet(RebacQuerySet[_ModelT]):
    """Ownership writes for verbs that have already authorized their selected rows."""

    def release(self, user: models.Model) -> int:
        """Clear only this user's ownership within this queryset, without another gate.

        Protected API: the caller must authorize the encompassing operation (for
        example, removal from a managed round). This is an elevated bulk write;
        it preserves attribution and emits no instance-save history.
        """

        if user.pk is None:
            raise ValidationError("Releasing ownership requires a saved user.")
        return self.filter(owner=user).system_context(reason="ownership.release").update(owner=None)


class ItemOwnershipMixin(models.Model):
    """Let a container retain ownership of newly created items through their parent access.

    Adopters gate updates with ``write__owns_items = share`` in their Zed schema.
    Changing the flag affects later inserts only.
    """

    owns_items = models.BooleanField(default=False)

    class Meta:
        abstract = True


class OwnerMixin(AuditMixin):
    """Give a grant root transferable ownership independent of its audit attribution.

    A non-null owner wins. Otherwise an owning container or ``save(ownerless=True)``
    leaves the owner empty; other inserts default to ``created_by`` and then the
    pinned acting user.
    A cached, saved container supplies its flag; an uncached container costs one
    query per insert. ``bulk_create`` bypasses this instance-save default.
    Compose :class:`OwnerQuerySet` when an owning verb needs bulk release.
    The owning model declares ``write__owner = <owner_transfer_permission>`` in
    Zed; multi-table children delegate that gate through their parent relation.
    Direct saves and queryset updates require the same transfer permission.
    """

    owner_transfer_permission: ClassVar[str] = "transfer"
    owner_container: ClassVar[str | None] = None
    """Foreign key to an ItemOwnershipMixin container, when this root is also an item."""
    owner_id: Any
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=audit_set_null,
        related_name="+",
    )

    class Meta:
        abstract = True

    def container_owns_items(self) -> bool:
        """Return the declared container's item-ownership policy, or false without one.

        Reuse a cached, saved container; otherwise read its flag through the
        system scope because this persistence rule is independent of read access.
        """

        if self.owner_container is None:
            return False
        field = self._meta.get_field(self.owner_container)
        container_id = getattr(self, field.attname)
        container = field.get_cached_value(self, default=None)
        if container is not None and not container._state.adding:
            return bool(container.owns_items)
        return container_id is not None and system_queryset(field.related_model).filter(
            pk=container_id, owns_items=True,
        ).exists()

    def save(self, *args: Any, ownerless: bool = False, **kwargs: Any) -> None:
        """Apply the insert-only owner default before the audit and permission hooks.

        ``ownerless=True`` requires a new row with no owner and skips only the
        owner default. Native audit, permission, history and save signals still
        run. To clear an existing owner, use ``transfer_ownership(None)``.
        """

        if ownerless and (not self._state.adding or self.owner_id is not None):
            raise ValidationError({"owner": "Ownerless insertion requires a new row with no owner."})
        if self._state.adding and self.owner_id is None and not ownerless:
            if not self.container_owns_items():
                self.owner_id = (
                    self.created_by_id if self.created_by_id is not None else actor_user_id(instance_actor(self))
                )
        super().save(*args, **kwargs)

    def transfer_ownership(self, user: models.Model | None) -> Self:
        """Authorize against the locked row, transfer or clear its owner, and retain attribution.

        Even ambient ``system_context`` must supply a resolvable actor: this verb
        owns an actor-authorized transfer, not an unattended ownership backfill.
        A concrete parent owns authorization when it declares the owner field.
        Constraints are validated against all rows before writing the new owner.
        A full save and refresh let composed mixins own their updated fields.
        """

        if self.pk is None or self._state.adding:
            raise ValidationError("Ownership transfer requires a saved row.")
        if user is not None and user.pk is None:
            raise ValidationError({"owner": "The new owner must be a saved user."})
        actor = instance_actor(self)
        if actor is None:
            raise PermissionDenied("Ownership transfer requires an acting user.")
        with transaction.atomic():
            locked = system_queryset(type(self), lock=("self",)).get(pk=self.pk).with_actor(actor)
            owner_model = locked._meta.get_field("owner").model
            target = locked
            if owner_model is not type(locked):
                target = system_queryset(owner_model).get(pk=locked._get_pk_val(owner_model._meta)).with_actor(actor)
            if not target.has_access(target.owner_transfer_permission):
                raise PermissionDenied("You cannot transfer ownership of this row.")
            if user is not None:
                locked.validate_record_access_subject(relation="owner", subject=user)
            owner_id = user.pk if user is not None else None
            if locked.owner_id != owner_id:
                locked.owner = user
                with system_context(reason="ownership.transfer.validate_constraints"):
                    locked.validate_constraints()
                locked.sudo(reason="ownership.transfer").save()
            self.refresh_from_db()
        return self


class _AppendOnlyWritesMixin(models.QuerySet[_ModelT]):
    """The closed collection-write policy shared by the scoped and base querysets.

    ``owner_update`` and ``owner_bulk_create`` are public, framework-protected
    APIs for domain owners that have already validated fields and predicates.
    They skip this class's guard only, preserving every downstream guard.
    """

    def immutable_error(self, operation: str) -> ValidationError:
        """Identify the model whose generic collection mutation is forbidden."""

        action = "deleted" if operation in {"delete", "_raw_delete"} else "edited"
        return ValidationError(f"{self.model._meta.label} rows cannot be {action}.")

    def validate_insert(self) -> None:
        """Let the domain owner narrow insert admission."""

    def bulk_create(
        self,
        objs: Iterable[_ModelT],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[_ModelT]:
        """Reject conflict handling before asking the owner to admit inserts."""

        if ignore_conflicts or update_conflicts:
            raise self.immutable_error("bulk_create")
        self.validate_insert()
        return self.owner_bulk_create(objs, batch_size=batch_size)

    def owner_bulk_create(self, objs: Iterable[_ModelT], *, batch_size: int | None = None) -> list[_ModelT]:
        """Protected API: insert an owner-validated batch through downstream guards."""

        return super().bulk_create(objs, batch_size=batch_size)

    def owner_update(self, **kwargs: Any) -> int:
        """Protected API: apply an owner-validated write through downstream guards."""

        return super().update(**kwargs)

    def update(self, **kwargs: Any) -> int:
        """Reject every collection edit."""

        raise self.immutable_error("update")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        """Reject every batched edit."""

        raise self.immutable_error("bulk_update")

    def delete(self) -> tuple[int, dict[str, int]]:
        """Reject deletion through the collection."""

        raise self.immutable_error("delete")

    def _raw_delete(self, using: str) -> int:
        """Reject direct SQL deletion through the queryset."""

        raise self.immutable_error("_raw_delete")


class AppendOnlyQuerySet(_AppendOnlyWritesMixin[_ModelT], RebacQuerySet[_ModelT]):
    """Close generic collection edits and deletion around owner-controlled writes.

    Compose before the domain's base queryset to preserve authorization.
    Retained evidence uses insert admission; retained state machines expose
    exact conditional writes through their own methods and ``owner_update``.
    Instance invariants and collector retention remain model/FK concerns;
    ``AuditMixin`` clears audit FKs through its collector policy without
    calling this queryset.
    """

    def insert(self, obj: _ModelT) -> _ModelT:
        """Validate one append before its ordinary authorized insertion."""

        self.validate_insert()
        return super().insert(obj)


class AppendOnlyBaseQuerySet(
    _AngeeQuerySetMixin[_ModelT], _AppendOnlyWritesMixin[_ModelT], TrackedQuerySet[_ModelT],
):
    """Unscoped rows for Django's own relation reads, with the same writes closed."""

    def validate_insert(self) -> None:
        """Apply the domain's default-queryset admission to base-manager inserts."""

        self.model._default_manager.get_queryset().validate_insert()


class AppendOnlyBaseManager(models.Manager.from_queryset(AppendOnlyBaseQuerySet)):  # type: ignore[misc]
    """The base manager an append-only model declares: unfiltered reads, closed writes."""


class AppendOnlyModel(models.Model):
    """Admit new rows while reserving retained-row changes for owner verbs.

    The model's default manager must compose :class:`AppendOnlyQuerySet`. An owner may
    use ``_owner_insert`` after its own admission or ``_owner_delete`` after its
    own retention check; ordinary instance and collection writes stay closed.
    """

    _append_only_base = AppendOnlyBaseManager()

    class Meta:
        abstract = True
        base_manager_name = "_append_only_base"

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Require the default manager to retain the append-only write contract."""

        errors = super().check(**kwargs)
        if not cls._meta.abstract and not isinstance(cls._default_manager.get_queryset(), AppendOnlyQuerySet):
            errors.append(
                checks.Error(
                    f"{cls._meta.label}'s default manager must compose AppendOnlyQuerySet.",
                    hint="Declare a default manager whose queryset inherits AppendOnlyQuerySet.",
                    obj=cls,
                    id="angee.E034",
                )
            )
        return errors

    def validate_append(self) -> None:
        """Let a domain owner narrow first insertion without replacing the guard."""

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError(f"{self._meta.label} rows cannot be edited.")
        self.validate_append()
        type(self)._default_manager.get_queryset().validate_insert()
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError(f"{self._meta.label} rows cannot be deleted.")

    def _owner_insert(self) -> None:
        """Insert one owner-admitted row, including domains with closed generic admission."""
        if not self._state.adding:
            raise ValidationError(f"{self._meta.label} rows cannot be edited.")
        self.validate_append()
        super().save(force_insert=True)

    def _owner_delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete one row after its domain owner has checked retention and locks."""
        return super().delete(*args, **kwargs)


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


class TrashMixin(models.Model):
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

    UNTRASHED_VALUES: ClassVar[Mapping[str, Any]] = MappingProxyType({
        TRASH_FLAG_FIELD: False,
        "trashed_at": None,
        "trashed_by": None,
        "trash_reason": "",
    })
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

    def trash(self, *, reason: str = "", using: str | None = None) -> None:
        """Move this row to the trash, stamping the acting user, time and reason."""

        reason = clean_trash_reason(reason)
        db = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=db):
            self._require_trash_state(trashed=False, using=db)
            self.is_trashed = True
            self.trashed_at = timezone.now()
            self.trashed_by_id = actor_user_id(instance_actor(self))
            self.trash_reason = reason
            self.save(using=db, update_fields=update_fields_with_auto_now(self, self.TRASH_FIELDS))

    def restore(self, *, using: str | None = None) -> None:
        """Take this row out of the trash, clearing its trash stamps."""

        db = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=db):
            self._require_trash_state(trashed=True, using=db)
            for name, value in self.UNTRASHED_VALUES.items():
                setattr(self, name, value)
            self.save(using=db, update_fields=update_fields_with_auto_now(self, self.TRASH_FIELDS))

    def _require_trash_state(self, *, trashed: bool, using: str) -> None:
        """Lock the stored row and refuse a trash verb that would change nothing."""

        stored = (
            system_queryset(type(self), lock=("self",))
            .using(using)
            .filter(pk=self.pk)
            .values_list(TRASH_FLAG_FIELD, flat=True)
            .first()
        )
        if stored is None:
            raise ValidationError(f"This {self._meta.verbose_name} no longer exists.")
        if bool(stored) is not trashed:
            state = "is not in" if trashed else "is already in"
            raise ValidationError({TRASH_FLAG_FIELD: f"This {self._meta.verbose_name} {state} the trash."})


class TrashQuerySet(models.QuerySet[_TrashModelT]):
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
        instance-save signals or history. ``at`` stamps rows trashed together.
        """

        now = timezone.now()
        return self.untrashed().update(
            **auto_now_stamps(self.model, now),
            is_trashed=True,
            trashed_at=at or now,
            trashed_by_id=actor_user_id(current_actor()),
            trash_reason=clean_trash_reason(reason),
        )

    def restore(self) -> int:
        """Bulk-restore this set's trashed rows; the caller has authorized every row.

        The protected counterpart of :meth:`trash`, with the same contract.
        """

        return self.trashed().update(**auto_now_stamps(self.model, timezone.now()), **TrashMixin.UNTRASHED_VALUES)


class ModelHistory(HistoricalRecords):
    """Native history adapted to abstract sources and generated module names.

    Tracking belongs to an abstract source/donor that includes HistoryMixin.
    Inheriting only a concrete tracked parent does not create a second history
    table for its MTI child. Native history construction receives a detached
    snapshot with stored gated values, never the caller's redacted projection.
    """

    def finalize(self, sender: type[models.Model], **kwargs: Any) -> None:
        history_source = cast(type[models.Model], self.cls)
        tracked_abstract_parent = False
        for candidate in sender.__bases__:
            if not isinstance(candidate, type) or not issubclass(candidate, models.Model):
                continue
            base = cast(type[models.Model], candidate)
            if base is not models.Model and issubclass(base, history_source) and base._meta.abstract:
                tracked_abstract_parent = True
                break
        if not tracked_abstract_parent:
            return
        super().finalize(sender, **kwargs)

    def create_historical_record(self, instance: models.Model, history_type: str, using: str | None = None) -> None:
        """Keep field redaction out of persisted history without changing the live instance."""

        gated = gated_read_fields(type(instance))
        attnames = [field.attname for field in self.fields_included(instance) if field.name in gated]
        if attnames:
            stored = system_queryset(type(instance)).values(*attnames).get(pk=instance.pk)
            instance = copy(instance)
            for attname, value in stored.items():
                setattr(instance, attname, value)
        super().create_historical_record(instance, history_type, using=using)

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
            if (
                source.remote_field is None
                or not source.auto_created
                or not source.remote_field.parent_link
            ):
                continue
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

    class Meta:
        abstract = True


class RevisionMixin(models.Model):
    """Track declared field snapshots through django-reversion."""

    revisioned_fields: ClassVar[tuple[str, ...]] = ()
    """Model field names registered with django-reversion."""

    class Meta:
        """Django model options for revision-only abstract inheritance."""

        abstract = True

    @property
    def revisions(self) -> Any:
        """Return this row's django-reversion versions newest-first."""

        versions = reversion.models.Version.objects.get_for_object(self)
        return versions.select_related("revision")

    def revert_to(self, version: Any) -> None:
        """Restore declared revisioned fields from ``version`` and save.

        Saves with ``update_fields`` so unrelated in-memory columns are not
        flushed. The method records its own revert revision so integrity does
        not depend on the caller's transport opening a reversion block.
        """

        data = version.field_dict
        reverted: list[str] = []
        for name in self.revisioned_fields:
            if name in data:
                setattr(self, name, data[name])
                reverted.append(name)
        if not reverted:
            return
        with transaction.atomic(), reversion.create_revision():
            self.save(update_fields=update_fields_with_auto_now(self, reverted))
            reversion.set_comment(f"Reverted to revision {version.revision_id}.")


class ImmutableFieldsMixin(models.Model):
    """Reject changes to loaded immutable attnames declared anywhere in the MRO.

    Donors contribute plain tuples. An owning verb permits its next write through
    ``allow_immutable_save``; unloaded deferred values remain untouched.
    """

    immutable_fields: ClassVar[tuple[str, ...]] = ()

    class Meta:
        """Django options for the shared write-once field guard."""

        abstract = True

    def allow_immutable_save(self, *field_names: str) -> None:
        """Allow the next save to change named immutable attnames."""

        allowed = set(getattr(self, "_allowed_immutable_fields", set()))
        allowed.update(field_names)
        self._allowed_immutable_fields = allowed

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist after comparing immutable facts with the committed row."""

        allowed = set(getattr(self, "_allowed_immutable_fields", set()))
        try:
            if self.pk is not None and not self._state.adding:
                declared = dict.fromkeys(
                    name for base in type(self).__mro__ for name in base.__dict__.get("immutable_fields", ())
                )
                checked = tuple(name for name in declared if name not in allowed and name in self.__dict__)
                if checked:
                    # Compare committed identities without loading unrelated deferred columns.
                    with system_context(reason=f"{self._meta.label_lower}.immutable_fields"):
                        persisted = type(self)._base_manager.filter(pk=self.pk).values(*checked).first()
                    if persisted is not None:
                        changed = [name for name in checked if persisted[name] != getattr(self, name)]
                        if changed:
                            raise ValidationError(
                                {name.removesuffix("_id"): "This identity or receipt is immutable." for name in changed}
                            )
            super().save(*args, **kwargs)
        finally:
            self._allowed_immutable_fields = set()


class RowLockMixin(models.Model):
    """Lock a row's system-owned fields against user writes.

    :meth:`locked_fields` names the fields a row's own loaded state locks; a row
    with any locked field is a system row. A user write may change only its
    unlocked fields: it cannot change a locked field, delete the row, or turn a
    new or unlocked row into a system row. System writers (provisioning and
    resource loads under ``system_context``) own system rows and are not
    checked. An elevation that only authorizes a user's write still checks:
    editable lines write their children elevated under the parent's write
    permission, so :class:`~angee.graphql.data.hasura.HasuraLines` calls
    :meth:`validate_row_lock` and :meth:`validate_row_delete` itself before each
    child write. Child nodes project the same fact as ``locked_fields``.
    """

    locked_rows_label: ClassVar[str | None] = None
    """Plural phrase naming the system rows in refusals; defaults to ``System <plural>``."""

    class Meta:
        """Django options for the shared system-row lock."""

        abstract = True

    def locked_fields(self) -> tuple[str, ...]:
        """Return the fields this row's loaded state locks; empty when the row is unlocked."""

        return ()

    def row_lock(self) -> dict[str, Any]:
        """Return this row's locked fields with their current values."""

        return {name: self._meta.get_field(name).value_from_object(self) for name in self.locked_fields()}

    def validate_row_lock(self, previous: Mapping[str, Any] | None) -> None:
        """Refuse this state as a user write over ``previous``, the row's persisted :meth:`row_lock`.

        ``previous`` is ``None`` (or empty) for a new or unlocked row, which a
        user write must not turn into a system row.
        """

        label = self._locked_rows_label()
        if previous:
            changed = [
                name for name, value in previous.items() if self._meta.get_field(name).value_from_object(self) != value
            ]
            if changed:
                raise ValidationError({
                    name: f"The {self._meta.get_field(name).verbose_name} of {label.lower()} cannot change."
                    for name in changed
                })
            return
        if locked := self.locked_fields():
            raise ValidationError(dict.fromkeys(locked, f"{label} are system-provisioned."))

    def validate_row_delete(self) -> None:
        """Refuse deleting this system row as a user write."""

        if self.locked_fields():
            raise ValidationError(f"{self._locked_rows_label()} cannot be deleted.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Check a user write against the committed row's lock before persisting it."""

        if not is_sudo():
            persisted = None
            if self.pk is not None and not self._state.adding:
                with system_context(reason=f"{self._meta.label_lower}.row_lock"):
                    persisted = type(self)._base_manager.filter(pk=self.pk).first()
            self.validate_row_lock(None if persisted is None else persisted.row_lock())
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Refuse a user deleting a committed system row."""

        if not is_sudo():
            with system_context(reason=f"{self._meta.label_lower}.row_lock"):
                persisted = type(self)._base_manager.filter(pk=self.pk).first()
            (persisted or self).validate_row_delete()
        return cast(tuple[int, dict[str, int]], super().delete(*args, **kwargs))

    def _locked_rows_label(self) -> str:
        """Return the plural phrase naming this model's system rows."""

        return self.locked_rows_label or f"System {self._meta.verbose_name_plural}"


class CreationKeyConflict(DomainError):
    """A client creation key was already used for different content."""

    code = "CREATION_KEY_CONFLICT"

    def __init__(self, *args: object) -> None:
        Exception.__init__(self, *args)


class CreationKeyQuerySet(models.QuerySet[_ModelT]):
    """Look up idempotent creations within the queryset's existing read policy."""

    def for_creation_key(self, scope: Any, key: str | None, fingerprint: str) -> _ModelT | None:
        """Return a readable replay, refusing content changes for the same key.

        A missing scope or key has no replay identity. Callers own fingerprint
        construction; :meth:`replay_or_insert` owns uniqueness races.
        An empty stored fingerprint is unknown legacy content and permits replay.
        """

        if key is not None:
            try:
                self.model._meta.get_field("client_creation_key").clean(key, None)
            except ValidationError as error:
                raise ValidationError({"client_creation_key": error}) from error
            if not key.strip():
                raise ValidationError({"client_creation_key": "A client creation key must not be blank."})
        if scope is None or key is None:
            return None
        model = cast(type[CreationKeyMixin], self.model)
        fields = model.creation_key_scope
        lookup = {fields: scope} if isinstance(fields, str) else dict(zip(fields, scope, strict=True))
        row = self.filter(**lookup, client_creation_key=key).first()
        if row is not None:
            cast(CreationKeyMixin, row).require_creation_fingerprint(fingerprint)
        return row

    def replay_or_insert(
        self, scope: Any, key: str | None, fingerprint: str, insert: Callable[[], _ModelT],
    ) -> tuple[_ModelT, bool]:
        """Return a readable replay or insert once, retrying a uniqueness race.

        The callback owns domain persistence and runs inside a savepoint. Only
        an existing matching receipt turns an insert/constraint failure into a
        replay. The boolean tells callers whether to run creation side effects.
        """

        self._for_write = True
        existing = self.for_creation_key(scope, key, fingerprint)
        if existing is not None:
            return existing, False
        try:
            with transaction.atomic(using=self.db):
                return insert(), True
        except (IntegrityError, ValidationError):
            existing = self.for_creation_key(scope, key, fingerprint)
            if existing is None:
                raise
            return existing, False


class CreationKeyMixin(models.Model):
    """Store a caller-scoped creation key and its original content fingerprint.

    Compose :class:`CreationKeyQuerySet` into the model's queryset and include
    :meth:`creation_key_constraint` in its ``Meta.constraints``. A custom scope
    is passed to the factory in that class body and declared on the model;
    the system check verifies they agree.
    """

    creation_key_scope: ClassVar[str | tuple[str, ...]] = "created_by"
    client_creation_key = models.CharField(max_length=128, null=True, blank=True, editable=False)
    creation_fingerprint = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        abstract = True

    @classmethod
    def creation_fingerprint_for(cls, values: Any) -> str:
        """Fingerprint native inputs; model references use immutable row identity."""

        def content(value: Any) -> Any:
            if isinstance(value, Enum):
                return content(value.value)
            if isinstance(value, models.Model):
                return {"model": value._meta.label_lower, "pk": json_safe(value.pk)}
            if isinstance(value, Mapping):
                return {key: content(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [content(item) for item in value]
            return json_safe(value)

        return canonical_json_sha256(content(values))

    def require_creation_fingerprint(self, fingerprint: str) -> None:
        """Validate a known receipt, including rows adopted from legacy partial work."""

        if self.creation_fingerprint and self.creation_fingerprint != fingerprint:
            raise CreationKeyConflict("This client creation key was already used for different content.")

    @classmethod
    def creation_key_actor_scope(cls, actor: Any, values: Mapping[str, Any]) -> Any:
        """Require a user-scoped creation receipt to belong to the acting user."""

        scope = actor_user_id(actor)
        if scope is None:
            raise ValidationError({"client_creation_key": "A creation key requires a user actor."})
        if not isinstance(cls.creation_key_scope, str):
            raise ValidationError({"client_creation_key": "A compound scope requires its domain creation verb."})
        scope_field = cls._meta.get_field(cls.creation_key_scope)
        supplied = values.get(scope_field.attname, values.get(scope_field.name, scope))
        supplied = supplied.pk if isinstance(supplied, models.Model) else supplied
        if supplied != scope:
            raise ValidationError({"client_creation_key": "The creation scope must be the current actor."})
        return scope

    @classmethod
    def creation_key_constraint(
        cls, *, scope: str | tuple[str, ...] | None = None, name: str | None = None,
    ) -> models.UniqueConstraint:
        """Declare key uniqueness, optionally preserving an adopter's existing constraint name."""

        scope = scope or cls.creation_key_scope
        fields = (scope,) if isinstance(scope, str) else scope
        return models.UniqueConstraint(
            fields=(*fields, "client_creation_key"),
            condition=models.Q(client_creation_key__isnull=False),
            name=name or "%(app_label)s_%(class)s_creation_key",
        )


class StaleRevisionError(DomainError):
    """An update expected a revision that is no longer committed.

    ``current`` is ``None`` when the row no longer exists.
    """

    code = "STALE_REVISION"

    def __init__(self, expected: int | None, current: int | None) -> None:
        self.expected = expected
        self.current = current
        Exception.__init__(self, f"Expected revision {expected}; current revision is {current}.")


def validate_revision(value: Any, *, minimum: int = 1) -> None:
    """Require a portable integer revision at or above its owner's minimum."""

    if type(value) is not int or not minimum <= value <= 2**31 - 1:
        raise ValidationError(
            {"expected_revision": f"Expected revision must be an integer from {minimum} to 2147483647."}
        )


def require_revision(*, expected: Any, current: int | None, minimum: int = 1) -> None:
    """Validate the owner's revision bounds and reject a stale expectation."""

    validate_revision(expected, minimum=minimum)
    if expected != current:
        raise StaleRevisionError(expected, current)


class OptimisticLockMixin(models.Model):
    """Count instance saves and optionally compare the revision in the UPDATE.

    Compose before the model base. Inserts start at one; nonempty updates bump
    through a database expression, including partial and deferred saves. Bulk
    queryset updates deliberately do not bump. A guarded compare-and-swap updates
    only the revision-owning table before Django saves the instance in the same
    transaction, even for multi-table children. New instances require an INSERT;
    loaded rows are never resurrected by ``save()``. On unguarded updates,
    ``pre_save`` receivers see an ``F()`` expression in the counter;
    ``post_save`` receivers and callers see the resulting integer.

    ``REVISION_FIELD`` names the counter. A model whose ``revision`` name is
    taken removes the inherited field with ``revision = None``, declares the
    same counter under another name and sets ``REVISION_FIELD`` to it; its
    GraphQL projection still exposes the counter as ``revision``.
    """

    REVISION_FIELD = "revision"
    """Name of the model field holding the save counter."""

    revision = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        abstract = True

    def require_revision(self, expected: Any) -> None:
        """Check a row already locked by an owning verb."""

        require_revision(expected=expected, current=getattr(self, self.REVISION_FIELD))

    def save(self, *, expected_revision: int | None = None, **kwargs: Any) -> None:
        """Save atomically, rejecting a competing update when an expectation is supplied."""

        if expected_revision is not None:
            validate_revision(expected_revision)
            if self._state.adding:
                raise ValidationError({"expected_revision": "An expected revision requires an existing row."})
        counter = self.REVISION_FIELD
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        owner = self._meta.get_field(counter).model
        rows = system_queryset(owner).using(using).filter(pk=self._get_pk_val(owner._meta))
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            kwargs["update_fields"] = update_fields
            if not update_fields:
                if expected_revision is not None:
                    current = rows.values_list(counter, flat=True).first()
                    require_revision(expected=expected_revision, current=current)
                return
        previous = self.__dict__.get(counter, models.DEFERRED)
        existing = not self._state.adding
        kwargs["using"] = using
        if existing:
            if update_fields is not None:
                kwargs["update_fields"] = update_fields | {counter}
            kwargs["force_update"] = True
        else:
            kwargs["force_insert"] = True
        try:
            with transaction.atomic(using=using):
                if expected_revision is not None:
                    updated = rows.filter(**{counter: expected_revision}).update(**{counter: F(counter) + 1})
                    if not updated:
                        current = rows.values_list(counter, flat=True).first()
                        raise StaleRevisionError(expected_revision, current)
                    setattr(self, counter, expected_revision + 1)
                elif existing:
                    setattr(self, counter, F(counter) + 1)
                super().save(**kwargs)
        except Exception as error:
            if previous is models.DEFERRED:
                self.__dict__.pop(counter, None)
            else:
                setattr(self, counter, previous)
            # Django doesn't pass force_update to MTI parents; a missing parent's
            # F-expression INSERT raises ValueError before that INSERT can execute.
            if (
                isinstance(error, (DatabaseError, ValueError))
                and existing and expected_revision is None and not rows.exists()
            ):
                raise StaleRevisionError(expected=None, current=None) from error
            raise


class HierarchyQuerySet(models.QuerySet[_HierarchyModelT]):
    """Subtree read scopes for models composing :class:`HierarchyMixin`.

    Compose alongside the model's base queryset (e.g.
    ``class LocationQuerySet(HierarchyQuerySet[Location], AngeeQuerySet[Location])``)
    and put it FIRST: the owner's path rewrite skips only this class's write
    guard through ``owner_update``, so any write-guarding
    queryset composed before it would be skipped too. The subtree vocabulary
    — :meth:`subtree_of` / :meth:`ancestors_of` — reads
    as chainable predicates over the maintained ``path`` column, served by the
    prefix index rather than a client-side ``parent`` walk.
    """

    def subtree_of(self, node: HierarchyMixin) -> Self:
        """Return ``node`` and every descendant (INCLUSIVE), by path prefix.

        A node's own ``path`` is the prefix of every descendant's path and of
        itself, so a single ``LIKE 'path%'`` covers the whole subtree. An
        unmaterialized ``node`` (empty ``path``) matches nothing rather than the
        whole table.
        """

        if not node.path:
            return cast(Self, self.none())
        return cast(Self, self.filter(path__startswith=node.path))

    def ancestors_of(self, node: HierarchyMixin) -> Self:
        """Return every proper ancestor of ``node`` (EXCLUSIVE of ``node``)."""

        return cast(Self, self.filter(path__in=node.ancestor_paths()))

    def update(self, **kwargs: Any) -> int:
        """Keep parent moves and derived paths on the saved-row owner."""

        if {"path", "parent", "parent_id"} & kwargs.keys():
            raise ValidationError("The hierarchy parent and path belong to the saved-row owner.")
        return super().update(**kwargs)

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[_HierarchyModelT]:
        """Require saves to derive paths from persisted row and parent identities."""

        raise ValidationError("Create hierarchy rows through save(); paths belong to the saved-row owner.")

    def owner_update(self, **kwargs: Any) -> int:
        """Protected API: apply an owner-validated write through downstream guards.

        HierarchyMixin owns the selected rows and derived path value; bypass
        only this class's external-write guard after validating the move.
        """

        return super().update(**kwargs)


class HierarchyMixin(models.Model):
    """Materialized-path tree membership for a self-parented model.

    Adds a ``parent`` self-FK and a maintained ``path`` column of zero-padded,
    delimiter-terminated primary-key segments (``/0000000012/0000000045/``), so
    subtree membership is a prefix test the database serves from an index rather
    than a fact each addon re-derives by walking ``parent`` in the client. The
    terminal delimiter is the correctness guarantee — a ``path`` is a string
    prefix of another exactly when the first node is an ancestor-or-self of the
    second — and the zero-padding (see :attr:`path_segment_width`) keeps segments
    lexically ordered.

    Compose it on a self-parented model, pair it with :class:`HierarchyQuerySet`
    for the ``subtree_of`` / ``ancestors_of`` read scopes, and **inherit its
    ``Meta``** so the concrete table carries the prefix index::

        class Location(HierarchyMixin, AngeeDataModel):
            ...

            class Meta(HierarchyMixin.Meta):
                abstract = False
                app_label = "inventory"
                rebac_resource_type = "inventory/location"

    (Django propagates ``Meta.indexes`` only through ``Meta``-class inheritance,
    not across sibling abstract bases, so a consumer that needs other indexes
    lists ``*HierarchyMixin.Meta.indexes`` alongside its own.)

    :meth:`save` maintains the path: derived from the parent on create, and on
    reparent it rejects a cycle (a new parent inside the node's own subtree) and a
    parent in a different scope (any field the model names in
    :attr:`hierarchy_scope_fields`), then rewrites the whole subtree's paths in one
    bulk ``UPDATE``. It owns no REBAC of its own; path maintenance runs unscoped so
    a reparent reaches descendants the acting user cannot read.
    """

    hierarchy_scope_fields: ClassVar[tuple[str, ...]] = ()
    """Field names a child must share with its parent (e.g. ``("scope",)``).

    A reparent (and a create under a parent) rejects a parent that differs on any
    of these fields, so a subtree never straddles a scope boundary. It is a
    declared contract — generic and iam-free — owned by the consuming model rather
    than probed by column name: a scoped tree declares
    ``hierarchy_scope_fields = ("scope",)``, an unscoped tree leaves it empty. An
    FK is compared by its stored id (the field's ``attname``); a parent must agree
    on every listed field.
    """

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    """The parent node, or ``NULL`` for a root; PROTECT keeps a subtree whole."""

    path = models.CharField(max_length=255, default="", editable=False)
    """Maintained root-to-self path of padded pk segments; server-owned.

    ``editable=False`` keeps it out of forms and the auto-CRUD write surface —
    the mixin is its only writer. The column width bounds tree depth: at
    :attr:`path_segment_width` = 12 each segment costs 13 characters, so
    ``max_length=255`` holds ~19 levels — deeper than any ERP location/category
    tree, but a consumer expecting deeper nesting must widen the column.
    Maintenance writes go through queryset ``update()`` (the one-UPDATE cascade),
    so ``path`` changes bypass ``post_save`` — a ``HistoryMixin`` consumer's
    historical rows do not track ``path``, a derivable server-owned value.
    """

    path_segment_width: ClassVar[int] = 12
    """Zero-pad width for one pk segment.

    Governs lexical ordering only; correctness rests on the terminal delimiter,
    so a primary key wider than this stays correct (it just sorts by raw digits
    within its level). Twelve digits order rows up to a trillion per table.
    """

    PATH_DELIMITER: ClassVar[str] = "/"
    """Segment delimiter; safe because a padded pk segment is digits only."""

    class Meta:
        """Abstract options carrying the prefix-serving ``path`` index."""

        abstract = True
        indexes = (PatternOpsIndex(fields=["path"], opclasses=["varchar_pattern_ops"]),)

    @classmethod
    def from_db(cls, db: Any, field_names: Sequence[str], values: Sequence[Any]) -> Self:
        """Record the loaded ``parent`` so :meth:`save` can detect a reparent.

        Only when ``parent`` was actually loaded: seeding the baseline off a
        deferred field (``.only(...)``/``.defer(...)`` excluding ``parent``)
        would trigger one extra query per row. :meth:`_hierarchy_needs_repath`
        falls back to the live ``parent_id`` when the baseline is absent, so a
        deferred load simply stays lazy.
        """

        instance = super().from_db(db, field_names, values)
        if "parent_id" in field_names:
            instance._hierarchy_saved_parent_id = instance.parent_id
        return instance

    def refresh_from_db(
        self,
        using: str | None = None,
        fields: Sequence[str] | None = None,
        from_queryset: models.QuerySet[Any] | None = None,
    ) -> None:
        """Reload the row, re-syncing the reparent baseline to the loaded ``parent``.

        Without the re-sync a refresh after an external ``parent`` change leaves
        the baseline stale, and the next unrelated ``save()`` would be
        misclassified as a reparent.
        """

        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        if fields is None or "parent" in fields or "parent_id" in fields:
            self._hierarchy_saved_parent_id = self.parent_id

    def ancestor_paths(self) -> list[str]:
        """Return the paths of this node's proper ancestors, root-first.

        Decomposes this node's own ``path`` into the cumulative prefixes at each
        delimiter boundary, dropping the last (the node itself) — so a root node
        yields an empty list.
        """

        delimiter = self.PATH_DELIMITER
        segments = [segment for segment in self.path.split(delimiter) if segment]
        prefix = delimiter
        paths: list[str] = []
        for segment in segments[:-1]:
            prefix += segment + delimiter
            paths.append(prefix)
        return paths

    def is_within(self, other: HierarchyMixin) -> bool:
        """Return whether this node is ``other`` or a descendant of ``other``.

        The test is intentionally inclusive and query-free: the maintained,
        delimiter-terminated ``path`` column is a prefix of exactly its own
        subtree. Empty/unmaterialized paths match nothing so they cannot become
        an accidental whole-tree prefix.
        """

        return bool(self.path and other.path and self.path.startswith(other.path))

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row, maintaining ``path`` on create and reparent."""

        if self._state.adding:
            self._save_created(*args, **kwargs)
        elif self._hierarchy_needs_repath():
            self._save_reparented(*args, **kwargs)
        else:
            super().save(*args, **kwargs)
        if "parent_id" in self.__dict__:
            self._hierarchy_saved_parent_id = self.parent_id

    def _save_created(self, *args: Any, **kwargs: Any) -> None:
        """Insert the row, then derive its ``path`` from the parent's committed path."""

        with transaction.atomic():
            super().save(*args, **kwargs)
            parent = self._hierarchy_parent()
            if parent is not None:
                # Re-read the parent's committed path under lock before deriving the
                # child prefix: a create racing a reparent of that parent would
                # otherwise bake in a stale prefix that the reparent's cascade never
                # reaches (the new row is not yet under the old prefix it rewrites).
                fresh = self._locked_paths([parent.pk])
                if parent.pk in fresh:
                    parent.path = fresh[parent.pk]
            self._reject_cross_scope_parent(parent)
            new_path = self._hierarchy_path(parent)
            if new_path != self.path:
                self._write_hierarchy_path(
                    system_queryset(type(self)).filter(pk=self.pk),
                    new_path,
                )
                self.path = new_path

    def _save_reparented(self, *args: Any, **kwargs: Any) -> None:
        """Validate the move under lock, then rewrite the subtree in one UPDATE."""

        # A reparent is defined by the moved ``parent``, so persist it (and the
        # derived ``path``) even under a partial ``update_fields`` that named
        # neither — otherwise the FK and the cascaded paths would diverge.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"parent", "path"}
        with transaction.atomic():
            old_path = self._lock_moved_paths()
            subtree = system_queryset(type(self), lock=("self",)).filter(path__startswith=old_path)
            if old_path:
                # Evaluate SELECT FOR UPDATE before moving any descendant's path.
                list(subtree.order_by("pk").values_list("pk", flat=True))
            parent = self._hierarchy_parent()
            self._reject_cycle(parent)
            self._reject_cross_scope_parent(parent)
            new_path = self._hierarchy_path(parent)
            self.path = new_path
            super().save(*args, **kwargs)
            if old_path:
                # One bulk UPDATE rewrites the old prefix on every row still
                # under it (descendants only — the save above already repathed
                # self) — never a per-row walk. The prefix is unique to this
                # root-to-self chain, so a whole-string REPLACE only touches the
                # head. An empty old path (an unmaterialized row) skips the
                # cascade: it has nothing under it, and ``LIKE '%'`` would
                # rewrite the whole table.
                replacement = Replace(F("path"), Value(old_path), Value(new_path))
                self._write_hierarchy_path(
                    subtree,
                    replacement,
                )

    def _write_hierarchy_path(
        self,
        queryset: models.QuerySet[Any],
        path_value: Any,
    ) -> int:
        """Rewrite the caller's system-scoped queryset inside its atomic block.

        Skip only the hierarchy's external-write guard; the remaining queryset
        chain, including REBAC, still owns authorization and persistence.
        """

        if isinstance(queryset, HierarchyQuerySet):
            return queryset.owner_update(path=path_value)
        return queryset.update(path=path_value)

    def _lock_moved_paths(self) -> str:
        """Row-lock this node and its new parent, refreshing committed paths.

        Two overlapping reparents interleaving on stale in-memory paths is the
        classic materialized-path hazard, so the moved node and its new parent are
        read under the queryset's row-lock owner and their committed paths replace
        the in-memory ones before validation and the cascade prefix derive from
        them. Returns this row's committed (old) path.
        """

        pks = [self.pk] if self.parent_id is None else [self.pk, self.parent_id]
        fresh = self._locked_paths(pks)
        self.path = fresh.get(self.pk, self.path)
        if self.parent_id is not None and self.parent_id in fresh:
            parent = self._hierarchy_parent()
            if parent is not None:
                parent.path = fresh[self.parent_id]
        return self.path

    def _locked_paths(self, pks: list[Any]) -> dict[Any, str]:
        """Return committed paths, serializing overlapping moves when supported."""

        reader = system_queryset(type(self), lock=("self",))
        return dict(reader.filter(pk__in=pks).values_list("pk", "path"))

    def _hierarchy_needs_repath(self) -> bool:
        """Return whether an existing row's ``parent`` moved (or its path is unset)."""

        # An unrelated deferred save must not load tree columns into its UPDATE.
        if "path" in self.__dict__ and not self.path:
            return True
        if "parent_id" not in self.__dict__:
            return False
        if hasattr(self, "_hierarchy_saved_parent_id"):
            return self._hierarchy_saved_parent_id != self.parent_id
        # A deferred load (``.only(...)`` excluding ``parent``) carries no baseline,
        # so a reparent would be invisible if we compared ``parent_id`` to itself.
        # Fetch the committed ``parent_id`` from the row to compare against the
        # in-memory FK the caller may have moved.
        return self._hierarchy_committed_parent_id() != self.parent_id

    def _hierarchy_committed_parent_id(self) -> Any:
        """Return this row's committed ``parent_id`` from the database."""

        return system_queryset(type(self)).filter(pk=self.pk).values_list("parent_id", flat=True).first()

    def _hierarchy_parent(self) -> HierarchyMixin | None:
        """Return the parent instance (cached when assigned), or ``None`` for a root."""

        if self.parent_id is None:
            return None
        return cast("HierarchyMixin", self.parent)

    def _hierarchy_path(self, parent: HierarchyMixin | None) -> str:
        """Return this node's derived path under ``parent`` (a root when ``None``)."""

        prefix = parent.path if parent is not None else self.PATH_DELIMITER
        return prefix + f"{self.pk:0{self.path_segment_width}d}{self.PATH_DELIMITER}"

    def _reject_cycle(self, parent: HierarchyMixin | None) -> None:
        """Reject a reparent whose new parent is this node or one of its descendants."""

        if parent is None or not self.path:
            return
        if parent.path.startswith(self.path):
            raise ValidationError({"parent": "A node cannot be moved under itself or a descendant."})

    def _reject_cross_scope_parent(self, parent: HierarchyMixin | None) -> None:
        """Reject a parent that differs on any declared :attr:`hierarchy_scope_fields`."""

        if parent is None:
            return
        for name in self.hierarchy_scope_fields:
            attname = self._meta.get_field(name).attname
            if getattr(self, attname) != getattr(parent, attname):
                raise ValidationError({"parent": f"Parent must belong to the same {name}."})
