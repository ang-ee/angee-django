"""Runtime model primitives shared by composed Angee applications."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, ClassVar, Self, TypeVar, cast

from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
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
from rebac.field_visibility import redacted_fields
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type, resource_id_attr
from simple_history.models import HistoricalChanges

from angee.actors import instance_actor
from angee.identity import PublicIdentity
from angee.impl import ImplClassField
from angee.models.identity import SqidMixin, TimestampMixin
from angee.models.inputs import InputExtensionsMixin
from angee.models.manager import AngeeManager, AngeeUnscopedManager
from angee.models.merge_references import MergeContractMixin, named_in_relationships, references
from angee.models.query import AngeeQuerySet, SystemQuerysetMixin, row_lock_paths
from angee.models.transitions import StateTransitions
from angee.permissions import effective_rebac_definition
from angee.signals import record_access_revoked
from angee.tiers import ResourceTier

_ModelT = TypeVar("_ModelT", bound=models.Model)
_SAVE_VERDICT = "_angee_save_verdict"
"""Instance key caching the save decision for the length of one ``full_clean``."""
_SAVE_PENDING = "pending"
"""The verdict while REBAC checks the save, so a clean that save runs skips the elevated checks."""


@dataclass(frozen=True, slots=True)
class DirectRecordAccess:
    """One direct declared-relation tuple on a shareable record."""

    relation: str
    subject: SubjectRef


class AngeeModel(
    InputExtensionsMixin, SystemQuerysetMixin, MergeContractMixin, PublicIdentity, TimestampMixin, RebacMixin
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

        # Record-edge policy composes MergeableMixin, which depends on this base.
        from angee.models.refs import reference_blocks_target_delete

        live_references = (
            reference
            for reference in references(type(self))
            if not issubclass(reference.model, HistoricalChanges)
            and reference_blocks_target_delete(reference, type(self))
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

    catalogue: bool = False
    """Whether this class declares itself as catalogue/reference data.

    The read is non-inherited: a subclass must declare ``catalogue = True`` on
    its own class body to opt in, matching ``runtime``'s structural-marker shape.
    """

    catalogue_tier: str = str(ResourceTier.MASTER)
    """Resource tier the catalogue rows belong to; read non-inherited."""

    catalogue_tiers: tuple[str, ...] | None = None
    """Optional allowed tiers for catalogues whose rows span install and demo."""

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

    def refresh_from_db(
        self,
        using: str | None = None,
        fields: Iterable[str] | None = None,
        from_queryset: models.QuerySet[Any] | None = None,
    ) -> None:
        """Reload committed fields through Django without treating hydration as a transition."""

        with StateTransitions._reload_state(self):
            super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)

    @property
    def record_display_label(self) -> str:
        """Return the record label used by generic GraphQL and record references."""

        return str(self)

    def full_clean(
        self,
        exclude: Iterable[str] | None = None,
        validate_unique: bool = True,
        validate_constraints: bool = True,
    ) -> None:
        """Clean as Django does; uniqueness and constraints share one access decision.

        See :meth:`constraint_check`.
        """

        self.__dict__[_SAVE_VERDICT] = None
        try:
            super().full_clean(exclude, validate_unique, validate_constraints)
        finally:
            self.__dict__.pop(_SAVE_VERDICT, None)

    def validate_unique(self, exclude: Collection[str] | None = None) -> None:
        """Check field uniqueness against every row once this row's save may proceed.

        See :meth:`constraint_check`.
        """

        if (check := self.constraint_check("validate_unique")) is not None:
            scope, unwritten = check
            with scope:
                super().validate_unique({*(exclude or ()), *unwritten} if unwritten else exclude)

    def validate_constraints(self, exclude: Collection[str] | None = None) -> None:
        """Check model constraints against every row once this row's save may proceed.

        See :meth:`constraint_check`.
        """

        if (check := self.constraint_check("validate_constraints")) is not None:
            scope, unwritten = check
            with scope:
                super().validate_constraints({*(exclude or ()), *unwritten} if unwritten else exclude)

    def constraint_check(self, step: str) -> tuple[AbstractContextManager[Any], frozenset[str]] | None:
        """Return how Django's ``step`` (``validate_unique`` or ``validate_constraints``) runs, or ``None``.

        Uniqueness and constraints compare this row with rows the actor may not
        read, so under an actor they run in a named system context, and only when
        this row's save would pass its REBAC gates (:meth:`_save_allowed`); otherwise
        ``None`` skips them, and the save refuses the row. For a saved row they leave
        out the fields the actor may not read (the second item): those were redacted
        when it loaded, and its save leaves them unwritten. They run as they are for
        a model without row policy, for a step with no rule that reads other rows,
        inside a system context, and with no actor (where strict mode refuses an
        actor-less read). An instance's own bypass (``sudo``) enters a system context.
        """

        if not model_resource_type(type(self)) or is_sudo() or not self._compares_other_rows(step):
            return nullcontext(), frozenset()
        if self.is_sudo():
            return system_context(reason=f"{self._meta.label_lower}.{step}"), frozenset()
        actor, _unscoped = self.effective_actor()
        if actor is None:
            return nullcontext(), frozenset()
        if not self._save_allowed():
            return None
        unwritten = frozenset() if self._state.adding else self.denied_read_fields()
        return system_context(reason=f"{self._meta.label_lower}.{step}"), unwritten

    def _compares_other_rows(self, step: str) -> bool:
        """Whether Django's ``step`` has a rule that reads rows other than this one."""

        lineage = (type(self), *self._meta.get_parent_list())
        if step == "validate_constraints":
            return any(model._meta.constraints for model in lineage)
        fields = self._meta.concrete_fields
        return (
            any(field.unique and not field.primary_key for field in fields)
            or any(
                getattr(field, option)
                for field in fields
                for option in ("unique_for_date", "unique_for_month", "unique_for_year")
            )
            or any(model._meta.unique_together for model in lineage)
        )

    def _save_allowed(self) -> bool:
        """Whether this row's save would pass its REBAC gates, decided once per :meth:`full_clean`.

        REBAC's ``check_save`` runs the save itself in a transaction it rolls
        back: the create candidate for a new row; ``write``, each changed field's
        ``write__`` gate, moving edges, expression reads and redacted writes for a
        saved one. A candidate it cannot evaluate (a related row that does not
        exist, a save that refuses its values or its actor) counts as refused, so
        its field errors stand. A clean that the checked save runs again sees no verdict yet.
        """

        verdict = self.__dict__.get(_SAVE_VERDICT)
        if verdict is not None:
            return verdict is True
        deciding = _SAVE_VERDICT in self.__dict__
        if deciding:
            self.__dict__[_SAVE_VERDICT] = _SAVE_PENDING
        try:
            verdict = self.check_save().allowed
        except DjangoPermissionDenied, ObjectDoesNotExist, TypeError, ValidationError, ValueError:
            verdict = False
        if deciding:
            self.__dict__[_SAVE_VERDICT] = verdict
        return verdict

    @classmethod
    def is_catalogue_model(cls) -> bool:
        """Return whether this class declares itself as catalogue data."""

        return bool(cls.__dict__.get("catalogue", False))

    @classmethod
    def get_catalogue_tier(cls) -> str:
        """Return this class's declared catalogue tier, defaulting to master."""

        return str(cls.__dict__.get("catalogue_tier", ResourceTier.MASTER))

    @classmethod
    def get_catalogue_tiers(cls) -> tuple[str, ...]:
        """Return every resource tier this catalogue accepts.

        Most catalogues belong to one tier.  A catalogue whose rows legitimately
        mix platform-required and optional examples may declare ``catalogue_tiers``
        while retaining ``catalogue_tier`` as its default authoring tier.
        """

        declared = cls.__dict__.get("catalogue_tiers")
        if declared is None:
            return (cls.get_catalogue_tier(),)
        return cast(tuple[str, ...], declared)

    @classmethod
    def get_rebac_grantable(cls) -> dict[str, str]:
        """Return and validate this model's declared record-share relations."""

        raw = cls.__dict__.get("rebac_grantable", {})
        if not isinstance(raw, Mapping):
            raise ImproperlyConfigured(f"{cls._meta.label}.rebac_grantable must be a mapping.")
        declaration: dict[str, str] = {}
        for relation, permission in raw.items():
            if not isinstance(relation, str) or not relation:
                raise ImproperlyConfigured(
                    f"{cls._meta.label}.rebac_grantable relation names must be non-empty strings."
                )
            if not isinstance(permission, str) or not permission:
                raise ImproperlyConfigured(f"{cls._meta.label}.rebac_grantable[{relation!r}] must name a permission.")
            declaration[relation] = permission
        return declaration

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
        :class:`angee.errors.RecordAccessSubjectRefused` to refuse a holder.
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
    def can_read_impl_choices(cls, field_name: str, actor: Any) -> bool:
        """Opt a field's implementation metadata into a model-owned actor policy.

        Console implementation metadata is administrator-only by default. A model
        may additionally authorize its own authors through their existing policy;
        this does not authorize reading or writing model records.
        """
        return False

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Run Django model checks plus Angee structural declaration checks."""

        errors = super().check(**kwargs)
        errors.extend(cls._check_catalogue_tier())
        errors.extend(cls._check_rebac_pk_identity())
        errors.extend(cls._check_rebac_grantable())
        return errors

    @classmethod
    def _check_catalogue_tier(cls) -> list[checks.CheckMessage]:
        """Return system-check errors for an invalid catalogue tier declaration."""

        if not cls.is_catalogue_model():
            return []
        default_tier = cls.get_catalogue_tier()
        declared = cls.__dict__.get("catalogue_tiers")
        tiers = (default_tier,) if declared is None else declared
        if (
            default_tier in ResourceTier.values
            and isinstance(tiers, tuple)
            and bool(tiers)
            and all(isinstance(tier, str) and tier in ResourceTier.values for tier in tiers)
            and len(set(tiers)) == len(tiers)
            and default_tier in tiers
        ):
            return []
        expected = ", ".join(repr(value) for value in ResourceTier.values)
        return [
            checks.Error(
                f"{cls._meta.label}.catalogue_tier must be a member of its nonempty, unique "
                f"catalogue_tiers tuple and every tier must be one of {expected}; "
                f"got default {default_tier!r} and tiers {tiers!r}.",
                obj=cls,
                id="angee.E014",
            )
        ]

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

    @classmethod
    def impl_key_for(cls, field_name: str, value: Any, *, default: str | None = None) -> str:
        """Return the canonical registry key for one ``ImplClassField`` value."""

        field = cls.impl_field(field_name)
        if value is None:
            if default is None:
                raise ValueError(f"{cls.__name__}.{field_name} requires an impl key.")
            value = default
        key = str(field.key_for(value) or "")
        if not key and default is not None:
            key = str(field.key_for(default) or "")
        field.resolve_class(key)
        return key

    def set_impl_key(self, field_name: str, value: Any, *, default: str | None = None) -> bool:
        """Assign an impl key and return whether the stored key changed."""

        field = type(self).impl_field(field_name)
        key = type(self).impl_key_for(field_name, value, default=default)
        changed = key != getattr(self, field.attname)
        if changed and field.create_only and not self._state.adding:
            raise ValidationError({field.name: "Implementation selection is create-only."})
        setattr(self, field.attname, key)
        return changed

    @classmethod
    def resolve_impl_class(cls, field_name: str, value: Any, *, default: str | None = None) -> type:
        """Return the impl class bound to one supplied impl-field value."""

        field = cls.impl_field(field_name)
        key = cls.impl_key_for(field_name, value, default=default)
        return field.resolve_class(key)

    @classmethod
    def impl_field(cls, field_name: str) -> ImplClassField[object]:
        """Return the declared ``ImplClassField`` named by ``field_name``.

        This is the model-owned accessor for callers that need the impl field's
        declared API without reaching through Django's raw ``_meta`` shape.
        """

        field = cls._meta.get_field(field_name)
        if not isinstance(field, ImplClassField):
            raise FieldDoesNotExist(f"{cls.__name__}.{field_name} is not an ImplClassField.")
        return field

    def resolve_impl(self, field_name: str, *, default: str | None = None) -> type:
        """Return the impl class selected by ``field_name`` on this instance."""

        field = type(self).impl_field(field_name)
        value = getattr(self, field.attname)
        if not value and default is not None:
            return field.resolve_class(default)
        return field.resolve_for(self)

    @classmethod
    def from_public_id(cls, value: str) -> Self | None:
        """Return the instance addressed by ``value``, if one exists."""

        queryset = cast(AngeeQuerySet[Self], cls._default_manager.all())
        return queryset.from_public_id(value)

    def broadcasts_changes(self) -> bool:
        """Return whether this row's saves/deletes broadcast on ``changes`` subscriptions.

        The publisher (:mod:`angee.graphql.publishing`) asks each row this before
        emitting a change event, so a model can keep some rows off the generic
        model-change subscription surface entirely — the emission mirror of a
        ``get_queryset`` read scope that hides them from the list. Evaluated while
        the instance is still live (a delete carries the in-memory row), so the
        answer holds for deletes too, which a post-hoc queryset membership check
        could not decide. Defaults to broadcasting; a model that isolates rows to a
        record-scoped surface (record chatter reachable only through
        ``record_thread``) overrides this to drop those rows.
        """

        return True


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
        # Marks the factory's output so the composer verifies the sys._getframe
        # module capture bound the anchor before emitting its import (see
        # ``Runtime._check_role_anchor_binding``); the wrapper hazard is caught here.
        "__angee_role_anchor__": True,
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
