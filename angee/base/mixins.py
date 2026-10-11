"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import copy
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum, StrEnum
from itertools import groupby
from types import MappingProxyType
from typing import Any, ClassVar, Self, TypeVar, cast

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import DatabaseError, IntegrityError, models, router, transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Replace
from django.utils import timezone
from rebac import PermissionDenied, RebacMixin, SubjectRef, actor_context, current_actor, system_context
from rebac.actors import is_sudo
from rebac.managers import RebacQuerySet, TrackedQuerySet
from rebac.resources import model_for_subject_type
from rebac.watch import by_key
from simple_history.manager import HistoryDescriptor, HistoryManager
from simple_history.models import HistoricalRecords
from simple_history.signals import pre_create_historical_record
from simple_history.utils import get_history_manager_for_model

from angee.base.actors import actor_user_id, instance_actor
from angee.base.autoconfig import SETTINGS
from angee.base.changesets import current_changeset, write_group
from angee.base.errors import DomainError
from angee.base.fields import SqidField, StateField
from angee.base.identity import PublicIdentity
from angee.base.indexes import PatternOpsIndex
from angee.base.managers import StatementManager, StatementPairManager
from angee.base.querysets import (
    AngeeUnscopedQuerySet,
    RetainedReferenceQuerySet,
    StatementQuerySet,
    _AngeeQuerySetMixin,
    model_history,
)
from angee.base.refs import concrete_child_models
from angee.base.scoping import read_scoped_queryset, system_queryset
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
    """Schedule retained-edge removal as system work inside Django's delete transaction."""

    rows: RetainedReferenceQuerySet[models.Model] = RetainedReferenceQuerySet(model=field.model, using=using)
    if isinstance(sub_objs, models.QuerySet):
        rows.query = sub_objs.query.chain()
        collector.add_field_update(field, None, rows)
    else:
        for batch in by_key(rows, [obj.pk for obj in sub_objs]):
            collector.add_field_update(field, None, batch)


setattr(retained_set_null, "lazy_sub_objs", True)


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
    return fields | set(_auto_now_fields(type(instance)))


def auto_now_stamps(model: type[models.Model], now: datetime) -> dict[str, Any]:
    """Return the ``auto_now`` columns a queryset update of ``model`` must stamp itself."""

    return dict.fromkeys(_auto_now_fields(model), now)


def _auto_now_fields(model: type[models.Model]) -> tuple[str, ...]:
    """Return the names of ``model``'s ``auto_now`` fields, which every write stamps."""

    return tuple(field.name for field in model._meta.concrete_fields if getattr(field, "auto_now", False))


class SqidMixin(PublicIdentity, models.Model):
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
        return (
            container_id is not None
            and system_queryset(field.related_model)
            .filter(
                pk=container_id,
                owns_items=True,
            )
            .exists()
        )

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
    _AngeeQuerySetMixin[_ModelT],
    _AppendOnlyWritesMixin[_ModelT],
    TrackedQuerySet[_ModelT],
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

    _angee_base = AppendOnlyBaseManager()

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"

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


class MergeContractMixin(models.Model):
    """Pointing-owner collision hook, shared by canonical and generated models."""

    merge_contract: ClassVar[Callable[..., None] | None] = None

    class Meta:
        abstract = True


class _ChangesetHistory(models.Model):
    """Historical attribution shared by every native generated history model."""

    changeset = models.ForeignKey(
        getattr(settings, "ANGEE_CHANGESET_MODEL", SETTINGS["ANGEE_CHANGESET_MODEL"]),
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
        """Choose the native row type before its single bulk insert.

        Upstream 3.13's constructor loop hardcodes '+'/'~' and exposes no row
        factory. The manager hook owns this small extension; history models keep
        Django's constructor and the library's fields, dates and relation shape.
        """

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
        super().__init__(*args, **kwargs)

    def finalize(self, sender: type[models.Model], **kwargs: Any) -> None:
        if sender._meta.abstract or sender._meta.proxy or not issubclass(sender, self.cls):
            return
        super().finalize(sender, **kwargs)
        sender._meta.angee_model_history = self
        pre_create_historical_record.connect(
            self.set_changeset,
            sender=get_history_manager_for_model(sender).model,
            weak=False,
        )

    def set_changeset(self, instance: models.Model, history_instance: models.Model, **kwargs: Any) -> None:
        """Bind attribution after native construction and before the history save."""

        history_instance.changeset = current_changeset(required=True, record=True)

    def get_history_user(self, instance: models.Model) -> models.Model | None:
        """Derive the user FK from the changeset's native subject, without a lookup."""

        context = current_changeset()
        actor = SubjectRef.parse(context.actor) if context is not None and context.actor else None
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
        for pk, grouped in groupby(rows, key=lambda row: getattr(row, key)):
            versions = list(grouped)
            first, last = versions[0], versions[-1]
            timeline = history.filter(**{key: pk})
            if timeline.filter(history_id__gt=last.history_id).exists():
                raise ValidationError(f"{model._meta.label} {pk}: a later write prevents revert.")
            previous = timeline.filter(history_id__lt=first.history_id).order_by("-history_id").first()
            if previous is None and first.history_type != "+":
                raise ValidationError(f"{model._meta.label} {pk}: the pre-boundary state is unavailable.")
            yield first, last, previous

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
            history_using = using if self.use_base_model_db else router.db_for_write(manager.model)
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
            alias = using or router.db_for_write(model, instance=instance)
            names = [field.attname for field in self.fields_included(model)]
            try:
                snapshot = model._base_manager.using(alias).only(*names).get(pk=instance.pk)
            except model.DoesNotExist as error:
                raise model.DoesNotExist(f"Cannot capture missing {model._meta.label} row {instance.pk!r}.") from error
            if hasattr(instance, "_history_date"):
                snapshot._history_date = instance._history_date
            super().create_historical_record(snapshot, history_type, using=using)
            manager = get_history_manager_for_model(model)
            self.record_rows(model, 1, using=alias if self.use_base_model_db else router.db_for_write(manager.model))

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
            if source.remote_field is None or not source.auto_created or not source.remote_field.parent_link:
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

    history = ModelHistory(inherit=True, bases=(_ChangesetHistory,))

    class Meta:
        abstract = True

    def save_base(
        self,
        raw: bool = False,
        force_insert: bool | tuple[type[models.Model], ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        """Validate attribution before SQL and scope native save dispatch to one group."""

        if not raw and not hasattr(self, "skip_history_when_saving"):
            if model_history(type(self)) is not None:
                current_changeset(required=True, record=True)
        alias = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=alias, savepoint=False), write_group():
            super().save_base(raw, force_insert, force_update, alias, update_fields)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Keep instance and collector receivers in one ephemeral write group."""

        history = model_history(type(self))
        if history is not None and not history.cascade_delete_history:
            current_changeset(required=True, record=True)
        with write_group():
            return super().delete(*args, **kwargs)


class SourceKind(models.TextChoices):
    """Source classification, never a global precedence order."""

    PERSON = "person", "Person"
    SYSTEM_OF_RECORD = "system_of_record", "System of record"
    DIRECTORY = "directory", "Directory"
    NETWORK = "network", "Network"
    IMPORT = "import", "Import"
    SEED = "seed", "Seed"
    DOCUMENT = "document", "Document"
    EXTRACTION = "extraction", "Extraction"
    RULE = "rule", "Rule"


class StatementSourceMixin(models.Model):
    """Principal contract for assertion-time, per-row source classification.

    Principals compose this mixin and implement ``source_kind`` as their own
    property, field or constant. Unknown principals cannot assert statements.
    """

    class Meta:
        abstract = True

    @property
    def source_kind(self) -> SourceKind:
        raise ImproperlyConfigured("Statement principals must implement source_kind.")

    @classmethod
    def kind_for(cls, source: SubjectRef) -> SourceKind:
        resolved = model_for_subject_type(source.subject_type)
        if resolved is None or source.optional_relation or source.subject_id == "*":
            raise ValidationError("A statement source must be one persisted principal.")
        model, id_attr = resolved
        if not issubclass(model, cls):
            raise ValidationError("The source must compose StatementSourceMixin.")
        try:
            principal = system_queryset(model).get(**{id_attr: source.subject_id})
            return SourceKind(principal.source_kind)
        except (model.DoesNotExist, ValueError, TypeError, ImproperlyConfigured) as error:
            raise ValidationError("The source must declare a known per-row source_kind.") from error


class Survivorship(StrEnum):
    """Strategies within an aspect, after preferred person decisions."""

    SOURCE_PRIORITY = "source_priority"
    MOST_RECENT = "most_recent"
    MOST_COMPLETE = "most_complete"
    HIGHEST_TRUST = "highest_trust"

    def __call__(self, date_field: str) -> RecentSurvivorship:
        """MOST_RECENT names the typed date column that owns recency."""

        if self != Survivorship.MOST_RECENT or not date_field:
            raise ValueError("Only MOST_RECENT(date_field) accepts a field.")
        return RecentSurvivorship(date_field)

    def sort_key(self, aspect: Aspect, row: Any) -> tuple[Any, ...]:
        priority = (
            aspect.precedence.index(row.source_kind) if row.source_kind in aspect.precedence else len(aspect.precedence)
        )
        rank, *trust = row.sort_key()
        tie = (rank, priority, *trust)
        if self == self.HIGHEST_TRUST:
            return row.sort_key()
        if self == self.MOST_COMPLETE:
            complete = sum(row._meta.get_field(name).value_from_object(row) not in (None, "") for name in aspect.fields)
            return (rank, -complete, priority, *trust)
        return tie


@dataclass(frozen=True)
class RecentSurvivorship:
    """The date-column parameter for MOST_RECENT."""

    date_field: str

    def sort_key(self, aspect: Aspect, row: Any) -> tuple[Any, ...]:
        value = row._meta.get_field(self.date_field).value_from_object(row)
        recency = value.timestamp() if isinstance(value, datetime) else value.toordinal() if value else 0
        rank, *tie = Survivorship.SOURCE_PRIORITY.sort_key(aspect, row)
        return (rank, value is None, -recency, *tie)


@dataclass(frozen=True)
class Aspect:
    """One field group and its optional typed membership identity.

    ``member`` reuses a canonical field name or maps names to new Django fields.
    Shared members reuse one field. ``allow_fill`` fills missing winner values.
    """

    fields: tuple[str, ...] = ()
    member: str | Mapping[str, models.Field] | None = None
    survivorship: Survivorship | RecentSurvivorship = Survivorship.SOURCE_PRIORITY
    precedence: tuple[SourceKind, ...] = ()
    allow_fill: bool = False

    @property
    def members(self) -> tuple[str, ...]:
        """Return deterministic member-column names for the aspect's key."""

        if isinstance(self.member, str):
            return (self.member,)
        return tuple(sorted(self.member or ()))

    def coerce_members(self, model: type[models.Model], source: SubjectRef, key: dict[str, Any]) -> dict[str, Any]:
        """Consume this aspect's complete identity through native field ownership."""

        members = {name: key.pop(name) for name in self.members if name in key}
        if set(members) != set(self.members) or any(value is None for value in members.values()):
            raise ValidationError("Every aspect member is required.")
        for name, value in members.items():
            field = model._meta.get_field(name)
            if isinstance(field, models.ForeignKey):
                pk = field.target_field.value_from_object(value) if isinstance(value, models.Model) else value
                members[name] = read_scoped_queryset(field.remote_field.model, source).get(
                    **{field.target_field.name: pk}
                )
            else:
                members[name] = field.to_python(value)
        return members


class StatementsMixin(models.Model):
    """Subject-owned eligibility and application hooks for typed statements."""

    aspect_precedence: ClassVar[Mapping[str, Sequence[str]]] = {}

    class Meta:
        abstract = True

    def statement_managers(self) -> Iterable[Any]:
        """Yield each MTI owner's bound table once, including parent declarations."""

        seen = set()
        for owner in (type(self), *self._meta.get_parent_list()):
            declaration = getattr(owner, "statements", None)
            if declaration is None or declaration.model in seen:
                continue
            seen.add(declaration.model)
            subject_model = declaration.model.statement_subject_model
            subject = (
                self
                if subject_model is type(self)
                else subject_model._base_manager.db_manager(self._state.db).get(pk=self.pk)
            )
            if subject is not self and (actor := instance_actor(self)) is not None:
                subject.with_actor(actor)
            yield subject.statements

    def statements_for(self, aspect: str) -> Any:
        """Return the single table declaring this aspect; conflicting owners refuse."""

        owners = [manager for manager in self.statement_managers() if aspect in manager.model.statement_aspects]
        if len(owners) != 1:
            raise ImproperlyConfigured(f"Expected one statement owner for {aspect!r}.")
        return owners[0]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Ordinary person edits assert normal statements; resolved saves stay ordinary."""

        if is_sudo():
            super().save(*args, **kwargs)
            return
        actor = instance_actor(self)
        if actor is None or StatementSourceMixin.kind_for(actor) != SourceKind.PERSON:
            super().save(*args, **kwargs)
            return
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        update_fields = kwargs.get("update_fields")
        with actor_context(actor), transaction.atomic(using=using):
            previous = type(self)._base_manager.using(using).filter(pk=self.pk).first() if self.pk else None
            super().save(*args, **kwargs)
            for manager in self.statement_managers():
                for aspect, declaration in manager.model.statement_aspects.items():
                    fields = declaration.fields
                    if (
                        declaration.members
                        or not fields
                        or not any(
                            (
                                update_fields is None
                                or name in update_fields
                                or self._meta.get_field(name).attname in update_fields
                            )
                            and (previous is None or getattr(previous, name) != getattr(self, name))
                            for name in fields
                        )
                    ):
                        continue
                    manager.set(
                        aspect=aspect,
                        values={name: getattr(self, name) for name in fields},
                        asserted_by=actor,
                    )
            self.refresh_from_db(using=using)

    def require_statement_write(self, actor: SubjectRef) -> None:
        """Admit assertions through the subject's native REBAC write scope."""

        rows = type(self)._default_manager.filter(pk=self.pk).with_actor(actor).with_action("write")
        if not rows.exists():
            raise PermissionDenied("The asserting source requires write on the subject.")

    def statement_candidates(self, aspect: str, candidates: Sequence[Any]) -> Sequence[Any]:
        """Select eligible normal candidates; preferred person decisions stay first."""
        return candidates

    def apply_resolved(
        self,
        *,
        aspect: str,
        member: Mapping[str, Any],
        values: Mapping[str, Any],
        statement: Any | None,
        expected_revision: int | None = None,
    ) -> None:
        if member:
            raise ImproperlyConfigured("Member aspects require the subject's apply_resolved hook.")
        changed = []
        for name, value in values.items():
            field = self._meta.get_field(name)
            value = field.get_default() if value is None and not field.null else value
            if getattr(self, field.attname) != value:
                setattr(self, field.attname, value)
                changed.append(name)
        if changed:
            kwargs: dict[str, Any] = {"update_fields": update_fields_with_auto_now(self, changed)}
            if isinstance(self, OptimisticLockMixin):
                kwargs["expected_revision"] = expected_revision
            self.save(**kwargs)

    @property
    def statement_revision(self) -> int | None:
        return getattr(self, self.REVISION_FIELD) if isinstance(self, OptimisticLockMixin) else None

    def provenance(self, field: str) -> Any:
        for manager in self.statement_managers():
            if any(field in (*aspect.fields, *aspect.members) for aspect in manager.model.statement_aspects.values()):
                return manager.provenance(field)
        raise FieldDoesNotExist(f"No aspect owns {field!r}.")

    def confirm(self, *, aspect: str, values: Mapping[str, Any], **members: Any) -> Any:
        manager = self.statements_for(aspect)
        return manager.set(
            aspect=aspect,
            values=values,
            rank=manager.model.Rank.PREFERRED,
            **members,
        )

    def dismiss(self, *, aspect: str, **members: Any) -> Any:
        manager = self.statements_for(aspect)
        return manager.set(
            aspect=aspect,
            values={},
            rank=manager.model.Rank.PREFERRED,
            snak=manager.model.Snak.NO_VALUE,
            **members,
        )

    def apply_pair(self, *, aspect: str, other: Any, statement: Any | None) -> None:
        raise ImproperlyConfigured("Pair aspects require the endpoint's apply_pair hook.")


class Statement(MergeContractMixin, HistoryMixin, _ChangesetHistory, SqidMixin, TimestampMixin, RebacMixin):
    """Retained shadow row; its canonical subject owns admission and field gates."""

    # Class names are the GraphQL enum names, so they carry the owner's prefix
    # (the example Note model already exposes a ``Status``); the short aliases are
    # the model-owned spelling the querysets read.
    class StatementSnak(models.TextChoices):
        VALUE = "value"
        SOME_VALUE = "some_value"
        NO_VALUE = "no_value"

    class StatementRank(models.TextChoices):
        PREFERRED = "preferred"
        NORMAL = "normal"

    class StatementStatus(models.TextChoices):
        ASSERTED = "asserted"
        WITHDRAWN = "withdrawn"
        QUARANTINED = "quarantined"
        DEPRECATED = "deprecated"

    Snak = StatementSnak
    Rank = StatementRank
    Status = StatementStatus

    SourceKind = SourceKind
    source_kind_for = StatementSourceMixin.kind_for
    objects = StatementManager()
    _angee_base = StatementManager()
    statement_subject_model: Any
    statement_aspects: dict[str, Aspect]
    statement_link: bool
    subject: Any
    asserted_by = models.TextField()
    source_kind = StateField(choices_enum=SourceKind)
    """Retained assertion-time classification, independent of later principal edits."""
    aspect = models.CharField(max_length=64)
    snak = StateField(choices_enum=Snak, default=Snak.VALUE)
    confidence = models.FloatField(default=1.0, validators=(MinValueValidator(0), MaxValueValidator(1)))
    rank = StateField(choices_enum=Rank, default=Rank.NORMAL)
    status = StateField(choices_enum=Status, default=Status.ASSERTED)
    applied = models.BooleanField(default=False)
    evidence = models.JSONField(default=list, blank=True)
    basis_hash = models.CharField(max_length=64, blank=True)
    superseded = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"
        rebac_resource_type = None

    @classmethod
    def stance_constraint(cls) -> models.CheckConstraint:
        return models.CheckConstraint(
            condition=Q(confidence__gte=0, confidence__lte=1)
            & (Q(rank=cls.Rank.NORMAL) | Q(rank=cls.Rank.PREFERRED, source_kind=SourceKind.PERSON))
            & Q(snak__in=cls.Snak.values, status__in=cls.Status.values, source_kind__in=SourceKind.values),
            name="%(app_label)s_%(class)s_stance",
        )

    @classmethod
    def valid_column(cls, name: str, field: Any) -> bool:
        """The generator omits invalid columns; check() reports their declarations."""

        reserved = {field.name for field in Statement._meta.fields} | {"subject", "link", "id", "pk", "history"}
        return (
            name not in reserved
            and name.isidentifier()
            and isinstance(field, models.Field)
            and not field.primary_key
            and not field.many_to_many
            and not isinstance(field, models.GeneratedField)
            and not (isinstance(field, models.ForeignKey) and field.remote_field.parent_link)
        )

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Validate declarations after all subject and relation models are prepared."""

        errors = super().check(**kwargs)
        if cls._meta.abstract:
            return errors

        def error(message: str, code: str) -> None:
            errors.append(checks.Error(message, obj=cls, id=f"angee.{code}"))

        if not issubclass(cls.statement_subject_model, StatementsMixin) or not cls.statement_aspects:
            error("StatementRecords requires StatementsMixin and at least one aspect.", "E039")
        claimed: set[str] = set()
        member_fields: dict[str, Any] = {}
        for key, aspect in sorted(cls.statement_aspects.items()):
            if not key or len(key) > 64 or not isinstance(aspect, Aspect):
                error(f"Invalid or conflicting aspect {key!r}; use a key of at most 64 characters.", "E040")
                continue
            if claimed.intersection(aspect.fields) or len(set(aspect.fields)) != len(aspect.fields):
                error("A value field belongs to exactly one aspect.", "E041")
            claimed.update(aspect.fields)
            if (
                not isinstance(aspect.survivorship, (Survivorship, RecentSurvivorship))
                or len(set(aspect.precedence)) != len(aspect.precedence)
                or any(kind not in SourceKind.values for kind in aspect.precedence)
                or aspect.survivorship == Survivorship.MOST_RECENT
            ):
                error(f"Invalid survivorship or precedence on {key!r}.", "E042")
            columns = {name: cls.statement_subject_model._meta.get_field(name) for name in aspect.fields}
            members = (
                aspect.member
                if isinstance(aspect.member, Mapping)
                else {name: cls.statement_subject_model._meta.get_field(name) for name in aspect.members}
            )
            for name, field in {**columns, **members}.items():
                if not cls.valid_column(name, field):
                    error(f"Invalid statement column {name!r}.", "E043")
                try:
                    cls._meta.get_field(name)
                except FieldDoesNotExist:
                    error(f"Aspect {key!r} claims excluded column {name!r}.", "E043")
            for name, field in members.items():
                if name in member_fields and member_fields[name] is not field:
                    error(f"Member {name!r} must reuse the same field.", "E041")
                member_fields[name] = field
            if isinstance(aspect.survivorship, RecentSurvivorship) and not isinstance(
                columns.get(aspect.survivorship.date_field), models.DateField
            ):
                error(f"Recency on {key!r} must name a date column in the aspect.", "E042")
        if claimed.intersection(member_fields):
            error("A member cannot also be a value column.", "E041")
        return errors

    trust_ordering: ClassVar[tuple[str, ...]] = ("-rank", "-confidence", "-updated_at", "-pk")

    def sort_key(self) -> tuple[Any, ...]:
        """Best rank, confidence, recency and identity first, including collisions."""

        values = {
            "rank": self.rank == self.Rank.PREFERRED,
            "confidence": self.confidence,
            "updated_at": self.updated_at.timestamp(),
            "pk": self.pk,
        }
        return tuple(-values[name.removeprefix("-")] for name in self.trust_ordering)

    def same_values(self, other: Statement, names: Iterable[str]) -> bool:
        return all(
            self._meta.get_field(name).value_from_object(self) == self._meta.get_field(name).value_from_object(other)
            for name in names
        )

    def current_key(self) -> dict[str, Any]:
        """The aspect's identity, shared by merge collapse and its inverse."""

        fields = ("subject", "asserted_by", "aspect", *self.statement_aspects[self.aspect].members)
        return {
            self._meta.get_field(name).attname: self._meta.get_field(name).value_from_object(self) for name in fields
        }

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not is_sudo():
            raise PermissionDenied("Write statements through their owning manager verbs.")
        self.changeset = current_changeset(required=True, record=True)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = update_fields_with_auto_now(self, (*kwargs["update_fields"], "changeset"))
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise ValidationError("Statements are retained; withdraw them instead.")

    @classmethod
    def merge_contract(cls, reference: Any, rows: Any, *, survivor: Any) -> None:
        cls.objects.merge(reference, rows, survivor=survivor)


class StatementPairQuerySet(TrashQuerySet, AngeeUnscopedQuerySet):
    """Retained pair carriers use the same removal scopes as canonical subjects."""


class StatementPair(
    MergeContractMixin, TrashMixin, StatementsMixin, HistoryMixin, SqidMixin, TimestampMixin, RebacMixin
):
    """Ordered pair subject; admission and application stay on its endpoints."""

    objects = StatementPairManager.from_queryset(StatementPairQuerySet)()
    _angee_base = StatementPairManager.from_queryset(StatementPairQuerySet)()
    left: Any
    right: Any
    pair_name: str

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"
        rebac_resource_type = None

    def require_statement_write(self, actor: SubjectRef) -> None:
        for endpoint in (self.left, self.right):
            endpoint.require_statement_write(actor)

    @classmethod
    def merge_contract(cls, reference: Any, rows: Any, *, survivor: Any) -> None:
        """Union third-party stances while retaining the merged pair as evidence."""

        cls.objects.merge(reference, rows, survivor=survivor)

    def apply_resolved(
        self,
        *,
        aspect: str,
        member: Mapping[str, Any],
        values: Mapping[str, Any],
        statement: Any | None,
        expected_revision: int | None = None,
    ) -> None:
        self.left.apply_pair(aspect=self.pair_name, other=self.right, statement=statement)


class StatementRecords(HistoricalRecords):
    """Generate typed assertions with simple-history's native model generator.

    A concrete MTI child declaring its own aspects gets its own statement table.
    An inherited parent declaration uses the parent's table through the shared pk.
    ``excluded_fields`` excludes copied subject fields, as in simple-history.
    ``link=False`` omits the optional RecordLink FK; the default includes it.
    Native ``bases``, ``history_manager`` and ``historical_queryset`` compose
    domain row behavior and member locking without another generator.
    """

    def __init__(
        self,
        *,
        aspects: Mapping[str, Aspect],
        sqid_prefix: str = "",
        excluded_fields: Sequence[str] = (),
        link: bool = True,
        bases: Sequence[type[models.Model]] = (Statement,),
        history_manager: type[StatementManager] = StatementManager,
        historical_queryset: type[StatementQuerySet] = StatementQuerySet,
    ) -> None:
        super().__init__(
            inherit=True,
            bases=bases,
            history_manager=history_manager,
            historical_queryset=historical_queryset,
            excluded_fields=excluded_fields,
            custom_model_name=lambda name: f"{name}Statement",
        )
        self.aspects = dict(aspects)
        self.sqid_prefix = sqid_prefix
        self.link = link

    def contribute_to_class(self, cls: Any, name: str) -> None:
        super().contribute_to_class(cls, name)
        # Keep source declarations available for additive abstract donors.
        # Native finalization replaces this with HistoryDescriptor on concrete rows.
        setattr(cls, name, self)

    def aspects_for(self, model: Any) -> dict[str, Any]:
        aspects: dict[str, Any] = {}
        donors = []
        for donor in reversed(model.__mro__):
            if donor is not model and (
                donor is models.Model
                or not issubclass(donor, models.Model)
                or not donor._meta.abstract
                or any(issubclass(parent, donor) for parent in model._meta.parents)
            ):
                continue
            donors.append(donor)
            declaration = self if donor is self.cls else donor.__dict__.get(self.manager_name)
            if isinstance(declaration, StatementRecords):
                for key, aspect in declaration.aspects.items():
                    # A conflicting declaration is reported by Statement.check().
                    aspects[key] = None if key in aspects and aspects[key] != aspect else aspect
        for donor in donors:
            for key, precedence in donor.__dict__.get("aspect_precedence", {}).items():
                if key not in aspects or not isinstance(aspects[key], Aspect):
                    raise ImproperlyConfigured(f"No declared aspect owns precedence {key!r}.")
                aspects[key] = replace(aspects[key], precedence=tuple(precedence))
        return aspects

    def finalize(self, sender: Any, **kwargs: Any) -> None:
        if sender._meta.abstract or sender._meta.proxy or not issubclass(sender, self.cls):
            return
        declaration = next(
            donor.__dict__[self.manager_name] for donor in sender.__mro__ if self.manager_name in donor.__dict__
        )
        if declaration is not self:
            return
        module = importlib.import_module(sender.__module__)
        name = self.get_history_model_name(sender)
        if name.lower() in sender._meta.apps.all_models[sender._meta.app_label] or hasattr(module, name):
            raise ImproperlyConfigured(f"Generated model {name} already exists.")
        model = self.create_history_model(sender, inherited=sender is not self.cls)
        setattr(module, name, model)
        setattr(sender, self.manager_name, HistoryDescriptor(model, self.history_manager, self.historical_queryset))

    def fields_included(self, model: Any) -> list[models.Field]:
        fields = {}
        for aspect in self.aspects_for(model).values():
            if not isinstance(aspect, Aspect):
                continue
            fields.update(
                {
                    name: model._meta.get_field(name)
                    for name in (*aspect.fields, *aspect.members)
                    if not isinstance(aspect.member, Mapping) or name not in aspect.member
                }
            )
            if isinstance(aspect.member, Mapping):
                for name, field in aspect.member.items():
                    if isinstance(field, models.Field):
                        field = copy(field)
                        field.set_attributes_from_name(name)
                        field.model = model
                        fields[name] = field
        return [
            field
            for name, field in sorted(fields.items())
            if name not in self.excluded_fields and Statement.valid_column(name, field)
        ]

    def copy_fields(self, model: Any) -> dict[str, models.Field]:
        fields = super().copy_fields(model)
        for field in fields.values():
            field.null = field.blank = True
            field.default = field.db_default = models.NOT_PROVIDED
            field.auto_created = False
            if isinstance(field, models.ForeignKey):
                field.remote_field.on_delete = models.PROTECT
                field.remote_field.parent_link = False
                field.db_constraint = True
        return fields

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        return {
            "__module__": model.__module__,
            "sqid_prefix": self.sqid_prefix,
            "statement_subject_model": model,
            "statement_aspects": self.aspects_for(model),
            "statement_link": self.link,
            "objects": self.history_manager(),
            "_angee_base": self.history_manager(),
            "subject": models.ForeignKey(model, on_delete=models.PROTECT, related_name="+"),
            "aspect": models.CharField(max_length=64, choices={name: name for name in sorted(self.aspects_for(model))}),
            **(
                {
                    "link": models.ForeignKey(
                        "integrate.RecordLink", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
                    )
                }
                if self.link
                else {}
            ),
        }

    def get_meta_options(self, model: Any) -> dict[str, Any]:
        constraints = [*Statement._meta.constraints, Statement.stance_constraint()]
        for key, aspect in sorted(self.aspects_for(model).items()):
            if not isinstance(aspect, Aspect):
                continue
            current = Q(aspect=key, superseded__isnull=True)
            for suffix, identity, condition in (
                ("key", ("asserted_by", "subject", "aspect", *aspect.members), current),
                ("applied", ("subject", "aspect", *aspect.members), current & Q(applied=True)),
            ):
                constraints.append(
                    models.UniqueConstraint(
                        fields=identity, condition=condition, name=f"%(app_label)s_%(class)s_{suffix}_{key}"
                    )
                )
            if aspect.members:
                constraints.append(
                    models.CheckConstraint(
                        condition=~Q(aspect=key) | Q(**{f"{member}__isnull": False for member in aspect.members}),
                        name=f"%(app_label)s_%(class)s_member_{key}",
                    )
                )
        constraints.append(
            models.CheckConstraint(
                condition=Q(aspect__in=tuple(sorted(self.aspects_for(model)))), name="%(app_label)s_%(class)s_aspect"
            )
        )
        return {
            "app_label": model._meta.app_label,
            "db_table": f"{model._meta.db_table}_statement",
            "base_manager_name": Statement._meta.base_manager_name,
            "rebac_resource_type": None,
            "constraints": constraints,
            "indexes": Statement._meta.indexes,
        }


class PairAspect(StatementRecords):
    """Declare a canonically ordered carrier with its own generated assertions."""

    def __init__(self, *, link: bool = True) -> None:
        super().__init__(aspects={}, link=link)
        self.bases = (StatementPair,)
        self.history_manager = StatementPairManager
        self.historical_queryset = StatementPairQuerySet
        self.custom_model_name = lambda name: (
            f"{name}{''.join(part.title() for part in self.manager_name.split('_'))}Pair"
        )

    def fields_included(self, model: Any) -> list[models.Field]:
        return []

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        return {
            "__module__": model.__module__,
            "pair_name": self.manager_name,
            "left": models.ForeignKey(model, on_delete=models.PROTECT, related_name="+"),
            "right": models.ForeignKey(model, on_delete=models.PROTECT, related_name="+"),
            "statements": StatementRecords(aspects={self.manager_name: Aspect()}, link=self.link),
        }

    def get_meta_options(self, model: Any) -> dict[str, Any]:
        return {
            "app_label": model._meta.app_label,
            "base_manager_name": StatementPair._meta.base_manager_name,
            "rebac_resource_type": None,
            "constraints": [
                models.UniqueConstraint(fields=("left", "right"), name="%(app_label)s_%(class)s_pair"),
                models.CheckConstraint(condition=Q(left__lt=F("right")), name="%(app_label)s_%(class)s_order"),
            ],
        }


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
                raise ValidationError(
                    {
                        name: f"The {self._meta.get_field(name).verbose_name} of {label.lower()} cannot change."
                        for name in changed
                    }
                )
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
        self,
        scope: Any,
        key: str | None,
        fingerprint: str,
        insert: Callable[[], _ModelT],
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
        except IntegrityError, ValidationError:
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
        cls,
        *,
        scope: str | tuple[str, ...] | None = None,
        name: str | None = None,
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
                and existing
                and expected_revision is None
                and not rows.exists()
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
