"""Runtime model primitives shared by composed Angee applications."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Self, TypeVar, cast

from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, router, transaction
from rebac import (
    RebacMixin,
    RelationshipTuple,
    SubjectRef,
    delete_relationship,
    to_object_ref,
    write_relationships,
)
from rebac.actors import is_sudo, to_subject_ref
from rebac.errors import NoActorResolvedError, PermissionDenied
from rebac.field_visibility import redacted_fields
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type, resource_id_attr

from angee.base.identity import PublicIdentity
from angee.base.managers import AngeeManager, AngeeUnscopedManager
from angee.base.models.identity import SqidMixin, TimestampMixin
from angee.base.models.inputs import InputExtensionsMixin
from angee.base.models.merge_references import MergeContractMixin, named_in_relationships, references
from angee.base.models.transitions import StateTransitionsMixin
from angee.base.permissions import declared_grants, effective_rebac_definition, instance_actor
from angee.base.querysets import row_lock_paths
from angee.base.signals import record_access_revoked

_ModelT = TypeVar("_ModelT", bound=models.Model)


@dataclass(frozen=True, slots=True)
class DirectRecordAccess:
    """One direct declared-relation tuple on a shareable record."""

    relation: str
    subject: SubjectRef


class AngeeModel(
    InputExtensionsMixin, StateTransitionsMixin, MergeContractMixin, PublicIdentity, TimestampMixin, RebacMixin
):
    """Abstract base model for Angee source and runtime models."""

    objects: ClassVar[models.Manager[Self]] = AngeeManager()
    """Default REBAC manager with Angee queryset conveniences."""

    _angee_base: ClassVar[models.Manager[Self]] = AngeeUnscopedManager()
    """Unfiltered tracked writes for Django's relation and base-manager paths."""

    def retained(self) -> bool:
        """Whether a live or historical reference, or a stored grant, names this row.

        This is evidence retention, distinct from a model's ``retains_merged``
        policy and an individual reference's ``retains`` predicate.
        """

        return named_in_relationships(self) or any(reference.retains(self) for reference in references(type(self)))

    def revert_removal(self, *, reason: str = "") -> None:
        """Remove an undone creation while preserving live references and grants.

        Native historical snapshots may outlive deleted targets. They retain a
        merge identity but do not prevent removal of its reverted creation.
        """

        live_references = (
            reference
            for reference in references(type(self))
            if reference.blocks_target_delete(type(self))
        )
        if named_in_relationships(self) or any(
            reference.rows(self).using(self._state.db).exists() for reference in live_references
        ):
            raise ValidationError(f"{self._meta.label} {self.pk}: live references or grants prevent removal.")
        self.delete()

    extends: ClassVar[str | None] = None
    """Optional ``app_label.ModelName`` target this source model extends."""

    runtime: bool = False
    """Whether this abstract source model materializes into the generated runtime.

    The read is non-inherited: an abstract base can stay ``runtime = False`` and
    a concrete source subclass opts in by declaring ``runtime = True`` itself.
    Extensions use ``extends`` instead of this flag.
    """

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

    def delete_blocker(self) -> str | None:
        """Return a public-safe deletion refusal, or ``None`` when allowed.

        Overrides require a ``pre_delete`` receiver enforcing the same rule on
        instance, queryset, and cascade deletes; previews only project it.
        """

        return None

    def lock_for_delete(self, *, queryset: models.QuerySet[models.Model] | None = None) -> Self | None:
        """Lock and reload this target for deletion in the model's domain order.

        Call inside a transaction, after any caller-owned permission preflight.
        The default locks only this row (including its concrete parents) through
        ``lock_if_supported``; overrides acquire domain locks before delegating.
        Confirmed-delete callers and ``pre_delete`` receivers must share this hook.
        A supplied ``queryset`` preserves the caller's scope and actor binding;
        receivers omit it to use system rows. Return ``None`` if unavailable.
        """

        if queryset is None:
            return type(self).system_queryset(lock=("self",)).filter(pk=self.pk).first()
        target = queryset.select_for_update(of=row_lock_paths(queryset.model)).filter(pk=self.pk).first()
        if target is not None and not isinstance(target, type(self)):
            raise TypeError("A delete queryset must return rows of its target model.")
        return target

    def redacted_fields(self) -> frozenset[str]:
        """Return the fields hidden when this instance was projected by REBAC."""

        return redacted_fields(self)

    @property
    def record_display_label(self) -> str:
        """Return the public record label shared by references and presentation."""

        return str(self)

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

    def _write_declared_record_access(self, relation: str, subject: models.Model | SubjectRef) -> None:
        """Validate the subject and write the declared relationship tuple."""

        self.validate_record_access_subject(relation, subject)
        write_relationships(
            [
                RelationshipTuple(
                    resource=to_object_ref(self),
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
        :class:`angee.base.permissions.RecordAccessSubjectRefused` to refuse a holder.
        The default accepts every subject. This hook decides no visibility and
        is never asked when revoking access or clearing ownership.
        """

        return None

    def require_access(self, permission: str, actor: Any = None) -> Any:
        """Authorize and return the explicit, pinned, or ambient actor, in that order.

        The resolved requester is retained as the instance binding. Missing actors
        are denied in every strict mode unless an explicit sudo scope is active.
        A concrete requester always clears instance sudo and scopes the check.
        """

        actor = actor or instance_actor(self)
        if actor is None:
            if self.is_sudo() or is_sudo():
                return None
            raise PermissionDenied(f"Denied: {permission!r} requires an actor.")
        self.with_actor(actor)
        if not self.has_access(permission):
            target = self._meta.label if self._state.adding else to_object_ref(self)
            raise PermissionDenied(f"Denied: the current actor lacks {permission!r} on {target}.")
        return actor

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
        definition = effective_rebac_definition(cls)
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



class AngeeDataModel(SqidMixin, AngeeModel):
    """Abstract base for Angee rows that participate in public data contracts."""

    class Meta:
        """Django model options for Angee's public data model base."""

        abstract = True


def record_display_label(record: models.Model) -> str:
    """Ask an Angee record for its label, preserving Django's string fallback."""

    return record.record_display_label if isinstance(record, AngeeModel) else str(record)


def role_anchor(
    resource_type: str,
    *,
    name: str | None = None,
    module: str | None = None,
    doc: str | None = None,
) -> type[AngeeModel]:
    """Return an abstract, table-less REBAC role anchor for ``resource_type``.

    A const-backed role relation (``admin: <ns>/role // rebac:const=admin`` in an
    addon's ``permissions.zed``) needs a model carrying that ``<ns>/role``
    ``rebac_resource_type`` so the ``rebac.E009`` system check resolves the type;
    the anchor is ``managed = False`` (Django owns no table, there are never any
    rows) and ``runtime = True`` (the composer materializes it into the generated
    runtime, exactly like the hand-rolled anchors it replaces). One adopter
    declares its role in one line::

        StorageRole = role_anchor("storage/role")

    ``name`` defaults to a CamelCase of ``resource_type`` (``storage/role`` ->
    ``StorageRole``); pass it when the module symbol differs from that default
    (e.g. ``TagRole = role_anchor("tags/role", name="TagRole")``). ``module``
    defaults to the caller's module (``sys._getframe``) so the composer scans and
    imports the anchor from the adopting addon; the module symbol you bind must
    match ``name`` so the emitted ``from <addon>.models import <name>`` import
    resolves. **Wrapper hazard:** the frame default captures the *direct* caller, so
    a helper that wraps this factory would capture the helper's module, not the
    adopter's, and emit an import that resolves to the wrong symbol. Call
    ``role_anchor`` directly at module level, or pass ``module=__name__`` when
    indirecting it. The composer verifies the captured module actually binds the
    anchor at emission (``angee.compose.rendering``) and fails loudly on a mis-capture
    rather than emitting a broken import.

    The ``.zed`` fragment stays **co-located and static** — each adopter ships its
    own ``definition <ns>/role`` block beside its models; the factory owns only
    the Django anchor model, never a composer ``.zed`` emission.

    Adopters declare their role in one line beside their own models — for
    example, framework ``storage`` (``StorageRole``) and ``tags`` (``TagRole``).
    """

    anchor_name = name or _role_anchor_name(resource_type)
    anchor_module = module or sys._getframe(1).f_globals.get("__name__", __name__)
    meta = type(
        "Meta",
        (),
        {
            "abstract": True,
            "managed": False,
            "rebac_resource_type": resource_type,
        },
    )
    namespace: dict[str, Any] = {
        "__module__": anchor_module,
        "__qualname__": anchor_name,
        "__doc__": doc or f"Table-less REBAC type anchor for the ``{resource_type}`` namespace.",
        "runtime": True,
        "Meta": meta,
    }
    return cast("type[AngeeModel]", type(anchor_name, (AngeeModel,), namespace))


def _role_anchor_name(resource_type: str) -> str:
    """Return the CamelCase anchor class name derived from a role resource type."""

    parts = [part for part in re.split(r"[^0-9A-Za-z]+", resource_type) if part]
    if not parts:
        raise ImproperlyConfigured(f"role_anchor: invalid resource_type {resource_type!r}")
    return "".join(part[:1].upper() + part[1:] for part in parts)


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
