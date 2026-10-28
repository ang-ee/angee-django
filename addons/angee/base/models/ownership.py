"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager
from typing import Any, ClassVar, Self, TypeVar

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models, transaction
from rebac import (
    PermissionDenied,
    RebacMixin,
    system_context,
)
from rebac.managers import RebacQuerySet
from rebac.resources import model_resource_type
from rebac.schema import AllowedSubject, FieldBinding, PermArrow, PermRef

from angee.base.permissions import actor_user_id, effective_rebac_definition, instance_actor
from angee.base.models.audit import AuditMixin, retained_set_null
from angee.base.models.base import AngeeModel
from angee.base.querysets import SystemQuerysetMixin

_ModelT = TypeVar("_ModelT", bound=models.Model)


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


class OwnerMixin(AuditMixin, SystemQuerysetMixin):
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

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Validate the ownership column, container and transfer gates this model owns."""

        errors = super().check(**kwargs)
        if cls._meta.abstract:
            return errors
        model = cls
        definition = effective_rebac_definition(model)
        owner_model = model._meta.get_field("owner").model
        assert issubclass(owner_model, OwnerMixin)
        if owner_model is model and model.owner_container is not None:
            try:
                field = model._meta.get_field(model.owner_container)
            except FieldDoesNotExist:
                field = None
            if (
                not isinstance(field, models.ForeignKey)
                or not isinstance(field.related_model, type)
                or not issubclass(field.related_model, ItemOwnershipMixin)
            ):
                errors.append(
                    checks.Error(
                        f"{model._meta.label}.owner_container must name a foreign key to an ItemOwnershipMixin model.",
                        obj=model,
                        id="angee.E025",
                    )
                )
        if owner_model is model and (
            definition is None
            or (
                not any(permission.name == model.owner_transfer_permission for permission in definition.permissions)
                or not any(
                    relation.name == "owner" and relation.backing == FieldBinding(path="owner")
                    for relation in definition.relations
                )
            )
        ):
            errors.append(
                checks.Error(
                    f"{model._meta.label} must declare an owner relation backed by owner "
                    f"and permission {model.owner_transfer_permission!r}.",
                    hint="Declare ownership and transfer on the grant root's effective permissions.zed.",
                    obj=model,
                    id="angee.E022",
                )
            )
        transfer = owner_model.owner_transfer_permission
        expected_gates: tuple[PermRef | PermArrow, ...] = (PermRef(transfer),)
        expected_expression = transfer
        # A child that shares its owning parent's definition is gated by that definition's own transfer.
        if owner_model is not model and model_resource_type(model) != model_resource_type(owner_model):
            parent_path = "__".join(path.join_field.name for path in model._meta.get_path_to_parent(owner_model))
            parent_type = model_resource_type(owner_model)
            parent_gates = (
                tuple(
                    PermArrow(relation.name, transfer)
                    for relation in definition.relations
                    if (
                        relation.backing == FieldBinding(path=parent_path)
                        and relation.allowed_subjects == (AllowedSubject(type=parent_type or ""),)
                    )
                )
                if definition is not None
                else ()
            )
            expected_gates = parent_gates
            expected_expression = " or ".join(f"{gate.via}->{gate.target}" for gate in parent_gates) or (
                f"<relation to {parent_type} backed by {parent_path}>->{transfer}"
            )
        if definition is None or not any(
            permission.name == "write__owner" and permission.expression in expected_gates
            for permission in definition.permissions
        ):
            errors.append(
                checks.Error(
                    f"{model._meta.label}: definition {model_resource_type(model)!r} must declare "
                    f"write__owner = {expected_expression}.",
                    hint="Gate owner writes on the owning model's transfer permission; "
                    "multi-table children delegate through their field-backed parent relation.",
                    obj=model,
                    id="angee.E027",
                )
            )
        return errors

    def container_owns_items(self) -> bool:
        """Return the declared container's item-ownership policy, or false without one.

        Reuse a cached, saved container; otherwise read its flag through the
        system scope because this persistence rule is independent of read access.
        """

        if self.owner_container is None:
            return False
        field = self._meta.get_field(self.owner_container)
        if not isinstance(field, models.ForeignKey):
            raise ImproperlyConfigured(f"{self._meta.label}.{self.owner_container} must be a ForeignKey.")
        container_model = field.related_model
        if container_model is None or not issubclass(container_model, ItemOwnershipMixin):
            raise ImproperlyConfigured("An owner container must compose ItemOwnershipMixin.")
        container_id = getattr(self, field.attname)
        container = field.get_cached_value(self, default=None)
        if container is not None and not container._state.adding:
            if not isinstance(container, ItemOwnershipMixin):
                raise ImproperlyConfigured("An owner container must compose ItemOwnershipMixin.")
            return bool(container.owns_items)
        return (
            container_id is not None
            and container_model.system_queryset()
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
            locked = type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk)
            if not isinstance(locked, AngeeModel):
                raise ImproperlyConfigured("Ownership transfer requires AngeeModel's record-access contract.")
            locked.with_actor(actor)
            owner_model = locked._meta.get_field("owner").model
            target: OwnerMixin = locked
            if owner_model is not type(locked):
                if not issubclass(owner_model, OwnerMixin):
                    raise ImproperlyConfigured("The owner field must be declared by OwnerMixin.")
                parent_pk = owner_model._meta.pk
                assert parent_pk is not None
                target = owner_model.system_queryset().using(using).get(
                    pk=parent_pk.value_from_object(locked),
                )
            if not isinstance(target, RebacMixin):
                raise ImproperlyConfigured("Ownership transfer requires a model composing RebacMixin.")
            target.with_actor(actor)
            if not target.has_access(target.owner_transfer_permission):
                raise PermissionDenied("You cannot transfer ownership of this row.")
            if user is not None:
                locked.validate_record_access_subject(relation="owner", subject=user)
                assert isinstance(user, get_user_model())
            owner_id = user.pk if user is not None else None
            if locked.owner_id != owner_id:
                locked.owner = user
                with system_context(reason="ownership.transfer.validate_constraints"):
                    locked.validate_constraints()
                locked.sudo(reason="ownership.transfer").save()
            self.refresh_from_db()
        return self
