"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator
from contextlib import contextmanager
from typing import Any, ClassVar, Self, TypeVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import (
    PermissionDenied,
    system_context,
)
from rebac.managers import RebacQuerySet

from angee.actors import actor_user_id, instance_actor
from angee.models.identity import update_fields_with_auto_now
from angee.models.query import RetainedReferenceQuerySet, SystemQuerysetMixin, by_key

_ModelT = TypeVar("_ModelT", bound=models.Model)


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


class ItemOwnershipMixin(SystemQuerysetMixin, models.Model):
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
        on_delete=retained_set_null,
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
            and field.related_model.system_queryset()
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
            self.owner_id = self.insert_owner_default()
        super().save(*args, **kwargs)

    def insert_owner_default(self) -> Any:
        """Return the owner an insert without one takes: its creator, else its acting user.

        None when an owning container holds its items' ownership.
        """

        if self.container_owns_items():
            return None
        return self.created_by_id if self.created_by_id is not None else actor_user_id(instance_actor(self))

    def validate_unique(self, exclude: Collection[str] | None = None) -> None:
        """Check uniqueness as the insert will save the row, with its owner default."""

        with self._insert_owner_default_held():
            super().validate_unique(exclude)

    def validate_constraints(self, exclude: Collection[str] | None = None) -> None:
        """Check constraints as the insert will save the row, with its owner default."""

        with self._insert_owner_default_held():
            super().validate_constraints(exclude)

    @contextmanager
    def _insert_owner_default_held(self) -> Iterator[None]:
        """Hold the owner an ordinary insert would save for the length of one check.

        The checks, and the create decision they wait on, then see the row as saved.
        The default is withdrawn afterwards, so ``save(ownerless=True)`` stays possible.
        """

        if not self._state.adding or self.owner_id is not None:
            yield
            return
        self.owner_id = self.insert_owner_default()
        try:
            yield
        finally:
            self.owner_id = None

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
        using = self._state.db
        with transaction.atomic(using=using):
            locked = type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk).with_actor(actor)
            owner_model = locked._meta.get_field("owner").model
            target = locked
            if owner_model is not type(locked):
                target = owner_model.system_queryset().using(using).get(
                    pk=locked._get_pk_val(owner_model._meta)
                ).with_actor(actor)
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
