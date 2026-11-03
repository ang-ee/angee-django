"""Runtime model primitives shared by composed Angee applications."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import nullcontext
from typing import Any, ClassVar, Self, TypeVar

from django.core import checks
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from rebac import (
    RebacMixin,
    RelationshipTuple,
    SubjectRef,
    delete_relationship,
    system_context,
    to_object_ref,
    write_relationships,
)
from rebac.actors import is_sudo, to_subject_ref
from rebac.errors import NoActorResolvedError, PermissionDenied
from rebac.field_backing import canonical_model
from rebac.field_visibility import redacted_fields
from rebac.managers import RebacManager
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type, resource_id_attr

from angee.base.models.base import AngeeDataModel, AngeeModel
from angee.base.models.persistence import PersistenceMixin, require_composed_type
from angee.base.signals import record_access_revoked
from angee.iam.managers import ProtectedManager, ProtectedUnscopedManager
from angee.iam.permissions import (
    DirectRecordAccess,
    actor_user_id,
    declared_grants,
    declared_rebac_definition,
    instance_actor,
    require_record_access,
)
from angee.iam.querysets import ProtectedQuerySet, ProtectedUnscopedQuerySet
from angee.iam.refs import named_in_relationships

_ModelT = TypeVar("_ModelT", bound=models.Model)


class ProtectedPersistenceMixin(PersistenceMixin):
    """Field-free system reads for models declaring native protected querysets.

    Compose this owner when a model needs IAM's persistence read contract without
    AngeeModel's fields or per-record RebacMixin behavior. Its source manager owns
    the queryset, database selection and filters; PersistenceMixin owns locks.
    """

    class Meta:
        abstract = True

    @classmethod
    def system_queryset(cls, *, lock: tuple[str, ...] | None = None) -> ProtectedQuerySet[Self]:
        """Keep the declared queryset while admitting owner-controlled system work."""
        rows = require_composed_type(super().system_queryset(lock=lock), ProtectedQuerySet)
        return rows.system_context(reason=f"{cls._meta.label_lower}.system_queryset")


class ProtectedModel(AngeeModel, ProtectedPersistenceMixin, RebacMixin, concrete=False):
    """IAM's native authorization contribution to the neutral model contract.

    Protected source declarations inherit this class when their Meta uses native
    REBAC options. IAMProtectionMixin donates that same native behavior and its
    queryset contracts to neutral source adopters when IAM is selected.
    """

    objects: ClassVar[RebacManager[Self]] = ProtectedManager()
    _angee_base: ClassVar[models.Manager[Self]] = ProtectedUnscopedManager()

    @classmethod
    def generic_pointer_model(cls) -> type[models.Model]:
        """Keep the native REBAC canonical identity on protected record pointers."""
        return canonical_model(cls) or super().generic_pointer_model()

    def bind_persistence_record(self, record: Self) -> Self:
        """Preserve native explicit actor precedence across persistence reloads."""
        record = super().bind_persistence_record(record)
        actor, elevated = self.effective_actor()
        if elevated:
            return record.sudo(reason="iam.persistence.admitted")
        if actor is not None:
            return record.with_actor(actor)
        return record.unsudo()

    def attributed_user_id(self) -> Any:
        return actor_user_id(instance_actor(self))

    @classmethod
    def retained_reference_update(cls, selected: models.QuerySet[Any], **values: Any) -> int:
        with system_context(reason="delete.retain"):
            return super().retained_reference_update(selected, **values)

    def save_children(
        self,
        field: str,
        rows: Iterable[tuple[str | None, dict[str, Any], dict[str, Any]]],
        *,
        replace: bool = True,
        **kwargs: Any,
    ) -> None:
        """Authorize owned replacements; nested creation retains its create authority."""
        if replace:
            self.require_access("write")
        with system_context(reason="iam.save_children"):
            super().save_children(field, rows, replace=replace, **kwargs)

    def retained(self) -> bool:
        """Include stored access relationships in the record's retention."""
        return named_in_relationships(self) or super().retained()

    def revert_removal(self, *, reason: str = "") -> None:
        """Refuse removal while a stored access relationship names the record."""
        if named_in_relationships(self):
            raise ValidationError(f"{self._meta.label} {self.pk}: stored grants prevent removal.")
        super().revert_removal(reason=reason)

    rebac_grantable: Mapping[str, str] = {}
    """Direct relations clients may manage, mapped to their required permission.

    Models opt in explicitly, for example ``{"reader": "share"}``. The
    composer carries the declaration onto the concrete runtime class and masks
    inherited declarations on materialized child models, so an undeclared model
    has no record-share surface.
    """

    class Meta:
        """Django model options for Angee's abstract model base."""

        abstract = True
        base_manager_name = "_angee_base"

    def redacted_fields(self) -> frozenset[str]:
        """Return the fields hidden when this instance was projected by REBAC."""

        return redacted_fields(self)

    @classmethod
    def get_rebac_grantable(cls) -> dict[str, str]:
        """Return and validate this model's declared record-share relations."""

        return declared_grants(cls)

    @classmethod
    def record_access_permission(cls, relation: str) -> str:
        """Return the permission required to manage one declared relation.

        An unknown relation is a hard error before any relationship tuple is
        constructed, making the share surface unable to mint undeclared tuples.
        """

        try:
            return cls.get_rebac_grantable()[relation]
        except KeyError as error:
            raise ValueError(f"{cls._meta.label} does not declare grantable relation {relation!r}.") from error

    def grant_record_access(self, relation: str, subject: models.Model | SubjectRef) -> None:
        """Idempotently grant ``subject`` one declared direct relation."""

        self.validate_record_access_target()
        self._grant_declared_record_access(type(self).record_access_permission, relation, subject)

    def _grant_declared_record_access(
        self,
        permission_for: Callable[[str], str],
        relation: str,
        subject: models.Model | SubjectRef,
    ) -> None:
        """Grant through one model class's own record-share declaration."""

        permission = permission_for(relation)
        self.require_access(permission)
        self._write_declared_record_access(relation, subject)

    def system_grant_record_access(self, relation: str, subject: models.Model | SubjectRef) -> None:
        """Grant one declared direct relation for a framework flow its owner already authorized.

        Requires an explicit system scope. The ambient actor is not consulted, because the
        flow's own owner check (for example a bridge's ``write``) is the authorization;
        the relation must still be declared and the subject valid for it.
        """

        if not is_sudo():
            raise PermissionDenied("A system record grant requires an explicit system scope.")
        self.validate_record_access_target()
        type(self).record_access_permission(relation)
        self._write_declared_record_access(relation, subject)

    def lock_for_record_access(self, *, using: str) -> Self:
        """Lock and reload this grant target in its owning domain's lock order.

        The write owner calls this inside its transaction. Contributors acquire
        prerequisite domain rows before delegating; validation itself stays a
        pure admission check usable by non-write callers.
        """

        return type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk)

    def _write_declared_record_access(self, relation: str, subject: models.Model | SubjectRef) -> None:
        """Keep fresh subject admission and the native relationship write under owner locks."""

        using = router.db_for_write(type(self), instance=self)
        relationships_using = router.db_for_write(active_relationship_model())
        with (
            transaction.atomic(using=using),
            transaction.atomic(using=relationships_using) if relationships_using != using else nullcontext(),
        ):
            target = self.bind_persistence_record(self.lock_for_record_access(using=using))
            target.validate_record_access_subject(relation, subject)
            write_relationships(
                [
                    RelationshipTuple(
                        resource=to_object_ref(target),
                        relation=relation,
                        subject=_relationship_subject(subject),
                    )
                ]
            )

    def revoke_record_access(self, relation: str, subject: models.Model | SubjectRef) -> None:
        """Idempotently revoke ``subject`` from one declared direct relation."""

        self.validate_record_access_target()
        self._revoke_declared_record_access(type(self).record_access_permission, relation, subject)

    def _revoke_declared_record_access(
        self,
        permission_for: Callable[[str], str],
        relation: str,
        subject: models.Model | SubjectRef,
    ) -> None:
        """Revoke through one model class's own record-share declaration."""

        permission = permission_for(relation)
        self.require_access(permission)
        self._delete_declared_record_access(relation, subject)

    def system_revoke_record_access(self, relation: str, subject: models.Model | SubjectRef) -> None:
        """Revoke a declared relation inside a framework flow's admitted system scope."""

        if not is_sudo():
            raise PermissionDenied("A system record revoke requires an explicit system scope.")
        self.validate_record_access_target()
        type(self).record_access_permission(relation)
        self._delete_declared_record_access(relation, subject)

    def _delete_declared_record_access(self, relation: str, subject: models.Model | SubjectRef) -> None:
        """Delete the tuple and notify its lifecycle owners in the same transaction."""

        subject_ref = _relationship_subject(subject)
        using = router.db_for_write(active_relationship_model())
        with transaction.atomic(using=using):
            delete_relationship(
                RelationshipTuple(
                    resource=to_object_ref(self),
                    relation=relation,
                    subject=subject_ref,
                )
            )
            record_access_revoked.send(
                sender=type(self),
                instance=self,
                relation=relation,
                subject=subject_ref,
                using=using,
            )

    def direct_record_access(self, relations: Sequence[str] | None = None) -> tuple[DirectRecordAccess, ...]:
        """Return authorized direct tuples for this record's declared relations.

        This deliberately reads only stored relationship rows. It does not walk
        usersets, groups, roles, relation arrows, or effective permissions.
        ``relations`` narrows both authorization and rows to a caller-authorized
        subset of the declared share surface.
        """

        declaration = type(self).get_rebac_grantable()
        if not declaration:
            raise ValueError(f"{type(self)._meta.label} declares no grantable relations.")
        selected = tuple(declaration) if relations is None else tuple(dict.fromkeys(relations))
        unknown = tuple(relation for relation in selected if relation not in declaration)
        if unknown:
            raise ValueError(f"{type(self)._meta.label} does not declare grantable relation {unknown[0]!r}.")
        self.validate_record_access_target()
        for permission in sorted({declaration[relation] for relation in selected}):
            self.require_access(permission)

        resource = to_object_ref(self)
        rows = (
            active_relationship_model()
            .objects.filter(
                resource_type=resource.resource_type,
                resource_id=resource.resource_id,
                relation__in=tuple(sorted(selected)),
            )
            .order_by("relation", "subject_type", "subject_id", "optional_subject_relation")
        )
        return tuple(
            DirectRecordAccess(
                relation=str(row.relation),
                subject=SubjectRef.of(
                    str(row.subject_type),
                    str(row.subject_id),
                    str(row.optional_subject_relation),
                ),
            )
            for row in rows
        )

    def validate_record_access_target(self) -> None:
        """Validate model-owned constraints on the record that receives direct grants."""

        return None

    def validate_record_access_subject(self, relation: str, subject: models.Model | SubjectRef) -> None:
        """Validate the record's invariant before a grant, assignment, or admission.

        Overrides call ``super()`` first and raise
        :class:`angee.base.exceptions.RecordAccessSubjectRefused` to refuse a holder.
        The default accepts every subject. This hook decides no visibility and
        is never asked when revoking access or clearing ownership.
        """

        return None

    def require_access(self, permission: str, actor: Any = None) -> Any:
        """Apply the foundation's actor-binding access gate."""
        return require_record_access(self, permission, actor)

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Run Django model checks plus Angee structural declaration checks."""

        errors = super().check(**kwargs)
        errors.extend(cls._check_rebac_pk_identity())
        errors.extend(cls._check_rebac_grantable())
        return errors

    @classmethod
    def _check_rebac_pk_identity(cls) -> list[checks.CheckMessage]:
        """Require table-backed Angee REBAC resources to use their primary key."""

        if not cls._meta.managed or model_resource_type(cls) is None:
            return []
        pk = cls._meta.pk
        pk_attname = pk.attname if pk is not None else "pk"
        if resource_id_attr(cls) in {"pk", pk_attname}:
            return []
        return [
            checks.Error(
                f"{cls._meta.label} must use its primary key as its REBAC identity; got {resource_id_attr(cls)!r}.",
                obj=cls,
                id="angee.E018",
            )
        ]

    @classmethod
    def _check_rebac_grantable(cls) -> list[checks.CheckMessage]:
        """Return system-check errors for invalid record-share declarations."""

        declaration = cls.get_rebac_grantable()
        if not declaration:
            return []

        resource_type = model_resource_type(cls)
        definition = declared_rebac_definition(cls)
        if definition is None:
            return [
                checks.Error(
                    f"{cls._meta.label}.rebac_grantable has no compiled zed definition for {resource_type!r}.",
                    obj=cls,
                    id="angee.E015",
                )
            ]

        relations = {relation.name: relation for relation in definition.relations}
        permissions = {permission.name for permission in definition.permissions}
        errors: list[checks.CheckMessage] = []
        for relation_name, permission_name in declaration.items():
            relation = relations.get(relation_name)
            if relation is None:
                errors.append(
                    checks.Error(
                        f"{cls._meta.label}.rebac_grantable relation {relation_name!r} "
                        f"is not defined on {resource_type!r}.",
                        obj=cls,
                        id="angee.E015",
                    )
                )
            elif relation.backing is not None:
                errors.append(
                    checks.Error(
                        f"{cls._meta.label}.rebac_grantable relation {relation_name!r} "
                        "must store direct tuples; backed relations cannot be granted.",
                        obj=cls,
                        id="angee.E016",
                    )
                )
            if permission_name not in permissions:
                errors.append(
                    checks.Error(
                        f"{cls._meta.label}.rebac_grantable permission {permission_name!r} "
                        f"is not defined on {resource_type!r}.",
                        obj=cls,
                        id="angee.E017",
                    )
                )
        return errors


class ProtectedDataModel(AngeeDataModel, ProtectedModel, concrete=False):
    """Public-data declaration base using IAM's native REBAC metaclass."""

    class Meta:
        abstract = True


def _relationship_subject(value: Any) -> SubjectRef:
    """Return one preflight relationship value as a REBAC subject reference."""

    if isinstance(value, SubjectRef):
        return value
    try:
        return to_subject_ref(value)
    except NoActorResolvedError:
        pass
    ref = to_object_ref(value)
    return SubjectRef.of(ref.resource_type, ref.resource_id)


class IAMProtectionMixin(ProtectedModel, extends="base.AngeeModel"):
    """Contribute native protection while retaining each source manager factory."""

    compose_querysets = {"objects": ProtectedQuerySet, "_angee_base": ProtectedUnscopedQuerySet}

    class Meta:
        abstract = True
