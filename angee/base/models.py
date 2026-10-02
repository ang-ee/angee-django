"""Runtime model primitives shared by composed Angee applications."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self, TypeVar, cast

from django.core import checks, signing
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from django.db.models.functions import Coalesce
from rebac import (
    RebacMixin,
    RelationshipTuple,
    SubjectRef,
    check_new,
    current_actor,
    delete_relationship,
    to_object_ref,
    write_relationships,
)
from rebac.actors import is_sudo, to_subject_ref
from rebac.errors import MissingActorError, NoActorResolvedError, PermissionDenied
from rebac.managers import RebacManager, RebacQuerySet, TrackedQuerySet
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type, resource_id_attr

from angee.base.actors import instance_actor
from angee.base.impl import ImplClassField
from angee.base.mixins import SqidMixin, TimestampMixin
from angee.base.pagination import KeysetOrder, KeysetPage
from angee.base.permissions import effective_rebac_definition
from angee.base.querysets import _AngeeQuerySetMixin
from angee.base.scoping import lock_if_supported
from angee.base.tiers import ResourceTier
from angee.base.transitions import StateTransitions

_ModelT = TypeVar("_ModelT", bound=models.Model)


@dataclass(frozen=True, slots=True)
class DirectRecordAccess:
    """One direct declared-relation tuple on a shareable record."""

    relation: str
    subject: SubjectRef


class AngeeQuerySet(
    _AngeeQuerySetMixin[_ModelT],
    RebacQuerySet[_ModelT],
):
    """QuerySet API shared by Angee source and runtime models."""

    def readable_scalar_subquery(
        self,
        field: str,
        *,
        actor: Any = None,
        default: Any = None,
        output_field: models.Field | None = None,
    ) -> models.Expression:
        """Project one correlated readable value, including under an elevated parent.

        Add correlation predicates before calling. No actor yields no value;
        callers own domain fallbacks and may explicitly coalesce an absent value.
        """

        actor = actor or self.actor() or current_actor()
        readable = self.with_actor(actor).scoped() if actor is not None else self
        # The selected field owns the scalar type; only the outer fallback needs
        # a common output type (overriding Subquery changes empty-set compilation).
        scalar = models.Subquery(readable.values(field)[:1])
        if actor is None:
            return models.Value(default, output_field=output_field or scalar.output_field)
        if default is None:
            return scalar
        return Coalesce(
            scalar,
            models.Value(default),
            output_field=output_field or scalar.output_field,
        )

    def readable_count_subquery(self, *, actor: Any = None) -> models.Expression:
        """Count a correlated actor-scoped row set, returning zero when empty."""

        rows = self.order_by().annotate(_count_group=models.Value(1)).values("_count_group")
        return rows.annotate(_count=models.Count("pk", distinct=True)).readable_scalar_subquery(
            "_count", actor=actor, default=0, output_field=models.IntegerField(),
        )

    def keyset_page(
        self,
        *,
        order: KeysetOrder,
        cursor_scope: tuple[Any, ...],
        cursor_salt: str = "angee.keyset.v1",
        before_cursor: str | None = None,
        after_cursor: str | None = None,
        through_cursor: str | None = None,
        around: models.Model | None = None,
        limit: int = 50,
    ) -> KeysetPage[_ModelT]:
        """Page this authorized queryset using signed, stable timestamp/PK cuts.

        The caller supplies a prepared, scoped queryset and canonical effective
        query identity in ``cursor_scope`` (root, filters and any variable order).
        Database, model and actor are always included. The salt versions the
        ordering contract. Cuts authorize nothing: each call reevaluates the
        readable queryset, including rows whose anchor was deleted or moved.

        ``before`` is exclusive; ``through`` is an inclusive fixed lower cut.
        ``after`` discovers the nearest newer page and cannot combine with either.
        Return rows newest-first and distinguish an exhausted fixed window from
        history below that window, even when the window is now empty.
        """

        if after_cursor is not None and (before_cursor is not None or through_cursor is not None):
            raise ValueError("after_cursor cannot combine with before_cursor or through_cursor.")
        limit = max(1, min(int(limit), 200))
        actor = self.actor() or current_actor()
        namespace = json.dumps([self.db, self.model._meta.label_lower, *cursor_scope, str(actor)])
        fingerprint = hashlib.sha256(namespace.encode()).hexdigest()
        signer = signing.Signer(salt=f"{cursor_salt}.{fingerprint}")
        cursor = before_cursor if before_cursor is not None else after_cursor
        anchor = order.unsign(cursor, signer, self.model._meta.pk) if cursor is not None else None
        lower = order.unsign(through_cursor, signer, self.model._meta.pk) if through_cursor is not None else None
        count = self.count()
        window = self
        if anchor is not None:
            window = window.filter(order.before(anchor) if before_cursor is not None else order.after(anchor))
        if lower is not None:
            window = window.exclude(order.before(lower))
        if around is not None and before_cursor is None and after_cursor is None:
            # Bound the first window around an already authorized record. The
            # domain resolves a message/date; this owner only handles tuple order.
            newer = list(
                self.filter(order.after(order.position(around))).order_by(order.field, "pk")[: limit // 2]
            )
            upper = order.position(newer[-1] if newer else around)
            window = window.exclude(order.after(upper))
        ascending = after_cursor is not None
        ordering = (order.field, "pk") if ascending else (f"-{order.field}", "-pk")
        selected = list(window.order_by(*ordering)[: limit + 1])
        has_more = len(selected) > limit
        rows = selected[:limit]
        if ascending:
            rows.reverse()
        below = self.filter(order.before(lower)).exists() if lower is not None else False
        if not rows:
            return KeysetPage(
                rows=[],
                count=count,
                older_cursor=None,
                newer_cursor=None,
                has_older=False,
                has_newer=False,
                has_more_in_window=False,
                has_older_than_through=below,
                has_newer_than_before=self.filter(order.after(anchor)).exists() if anchor is not None else False,
            )
        # Re-fetch the bounded identities through the original queryset so callers
        # retain a composable queryset instead of inheriting this method's probe list.
        return KeysetPage(
            rows=self.filter(pk__in=[row.pk for row in rows]).order_by(f"-{order.field}", "-pk"),
            count=count,
            older_cursor=order.sign(rows[-1], signer),
            newer_cursor=order.sign(rows[0], signer),
            has_older=self.filter(order.before(order.position(rows[-1]))).exists(),
            has_newer=self.filter(order.after(order.position(rows[0]))).exists(),
            has_more_in_window=has_more,
            has_older_than_through=below,
            has_newer_than_before=self.filter(order.after(anchor)).exists() if anchor is not None else False,
        )


class AngeeUnscopedQuerySet(
    _AngeeQuerySetMixin[_ModelT],
    TrackedQuerySet[_ModelT],
):
    """Angee queryset API for intentionally permission-naive managers.

    Used by models without REBAC row policy and explicit Django base managers
    whose unfiltered relation reads must retain native Django semantics. It
    composes the library's tracked writes required by a declared base manager.
    """

    def scoped_for_aggregate(self) -> Self:
        """Return this queryset for permission-naive aggregation.

        The manager deliberately supplies no row authorization; aggregation
        preserves that same explicit unscoped policy.
        """

        return self


class AngeeManager(RebacManager.from_queryset(AngeeQuerySet)):  # type: ignore[misc]
    """Manager backed by AngeeQuerySet."""

    def get_queryset(self) -> AngeeQuerySet[Any]:
        """Return the base Angee queryset for this manager's model."""

        return cast(AngeeQuerySet[Any], super().get_queryset())

    def check_create(
        self,
        relationships: Mapping[str, Sequence[Any]] | None = None,
    ) -> SubjectRef:
        """Authorize the ambient actor to create one not-yet-persisted row.

        Manual factories use this explicit relationship preflight
        (``rebac.check_new``), insert under per-instance sudo, and re-bind the
        verified actor with ``with_actor`` so the bypass ends with that insert.
        Ordinary ``create`` and prepared-instance ``insert`` instead rely on
        REBAC's candidate-aware pre-save gate.

        ``relationships`` values may be model instances or ``SubjectRef``s;
        instances are resolved through their declared REBAC resource type.
        Returns the verified actor; raises ``MissingActorError`` without an
        ambient actor and ``PermissionDenied`` when the gate refuses.
        """

        actor = current_actor()
        resource_type = model_resource_type(self.model)
        if not resource_type:
            raise ImproperlyConfigured(f"{self.model._meta.label} declares no rebac_resource_type")
        if actor is None:
            raise MissingActorError(f"Creating {resource_type} requires an actor.")
        result = check_new(
            subject=actor,
            action="create",
            resource_type=resource_type,
            relationships={
                relation: tuple(_relationship_subject(value) for value in values)
                for relation, values in (relationships or {}).items()
            },
        )
        if not result.allowed:
            raise PermissionDenied(f"Denied: {actor} cannot create {resource_type}")
        return actor


class AngeeUnscopedManager(models.Manager.from_queryset(AngeeUnscopedQuerySet)):  # type: ignore[misc]
    """Manager backed by AngeeUnscopedQuerySet."""

    def get_queryset(self) -> AngeeUnscopedQuerySet[Any]:
        """Return the base unscoped Angee queryset for this manager's model."""

        return cast(AngeeUnscopedQuerySet[Any], super().get_queryset())


class AngeeModel(TimestampMixin, RebacMixin):
    """Abstract base model for Angee source and runtime models."""

    objects = AngeeManager()
    """Default REBAC manager with Angee queryset conveniences."""

    extends: str | None = None
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

    catalogue_tier: str = ResourceTier.MASTER
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

    def delete_blocker(self) -> str | None:
        """Return a public-safe deletion refusal, or ``None`` when allowed.

        Overrides require a ``pre_delete`` receiver enforcing the same rule on
        instance, queryset, and cascade deletes; previews only project it.
        """

        return None

    def lock_for_delete(self, *, queryset: models.QuerySet[Self] | None = None) -> Self | None:
        """Lock and reload this target for deletion in the model's domain order.

        Call inside a transaction, after any caller-owned permission preflight.
        The default locks only this row (including its concrete parents) through
        ``lock_if_supported``; overrides acquire domain locks before delegating.
        Confirmed-delete callers and ``pre_delete`` receivers must share this hook.
        A supplied ``queryset`` preserves the caller's scope and actor binding;
        receivers omit it to use system rows. Return ``None`` if unavailable.
        """

        targets = queryset if queryset is not None else type(self).system_queryset()
        return lock_if_supported(targets).filter(pk=self.pk).first()

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

    @classmethod
    def system_queryset(
        cls,
        *,
        lock: tuple[str, ...] | None = None,
    ) -> AngeeQuerySet[Self]:
        """Return an elevated unscoped queryset with backend-gated locks; SQLite stays unlocked.

        Locking was previously dead at these call sites and is now real.
        """

        queryset = cast(
            AngeeQuerySet[Self],
            cls._default_manager.get_queryset(),
        ).system_context(reason=f"{cls._meta.label_lower}.system_queryset")
        return queryset.lock_if_supported(of=lock) if lock is not None else queryset

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
        self._grant_declared_record_access(type(self), relation, subject)

    def _grant_declared_record_access(
        self,
        declaration_owner: type[AngeeModel],
        relation: str,
        subject: models.Model | SubjectRef,
    ) -> None:
        """Grant through one model class's own record-share declaration."""

        permission = declaration_owner.record_access_permission(relation)
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
        self._revoke_declared_record_access(type(self), relation, subject)

    def _revoke_declared_record_access(
        self,
        declaration_owner: type[AngeeModel],
        relation: str,
        subject: models.Model | SubjectRef,
    ) -> None:
        """Revoke through one model class's own record-share declaration."""

        permission = declaration_owner.record_access_permission(relation)
        self.require_access(permission)
        delete_relationship(
            RelationshipTuple(
                resource=to_object_ref(self),
                relation=relation,
                subject=_relationship_subject(subject),
            )
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
            raise ValueError(
                f"{type(self)._meta.label} does not declare grantable relation {unknown[0]!r}."
            )
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
        :class:`angee.base.errors.RecordAccessSubjectRefused` to refuse a holder.
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
                f"{cls._meta.label} must use its primary key as its REBAC identity; "
                f"got {resource_id_attr(cls)!r}.",
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

    @classmethod
    def resolve_impl_class(cls, field_name: str, value: Any, *, default: str | None = None) -> type:
        """Return the impl class bound to one supplied impl-field value."""

        field = cls.impl_field(field_name)
        key = cls.impl_key_for(field_name, value, default=default)
        return field.resolve_class(key)

    @classmethod
    def impl_field(cls, field_name: str) -> Any:
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

    @property
    def public_id(self) -> str:
        """Return the stable public identifier for this model instance."""

        value = self.public_id_value()
        if value in (None, ""):
            return ""
        return str(value)

    @classmethod
    def from_public_id(cls, value: str) -> Self | None:
        """Return the instance addressed by ``value``, if one exists."""

        queryset = cast(AngeeQuerySet[Self], cls._default_manager.all())
        return queryset.from_public_id(value)

    @classmethod
    def public_id_lookup(cls, value: str) -> dict[str, Any]:
        """Return the Django lookup for this model's public identifier."""

        return {cls._meta.pk.name: value}

    @classmethod
    def public_id_from_pk(cls, value: Any) -> str:
        """Return the public id encoded from this model's primary-key value."""

        if value in (None, ""):
            return ""
        return str(value)

    def public_id_value(self) -> Any:
        """Return the raw public identifier value owned by this instance."""

        return self.pk

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
