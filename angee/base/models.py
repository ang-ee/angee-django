"""Runtime model primitives shared by composed Angee applications."""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Self, TypeVar, cast

from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import connections, models
from django.db.models.signals import class_prepared, post_delete
from django.db.models.utils import make_model_tuple
from rebac import (
    RebacMixin,
    RelationshipTuple,
    SubjectRef,
    check_new,
    current_actor,
    delete_relationship,
    delete_relationships,
    to_object_ref,
    write_relationships,
)
from rebac.actors import to_subject_ref
from rebac.errors import MissingActorError, NoActorResolvedError, PermissionDenied
from rebac.managers import RebacManager, RebacQuerySet
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type
from rebac.types import RelationshipFilter

from angee.base.impl import ImplClassField
from angee.base.mixins import SqidMixin, TimestampMixin
from angee.base.permissions import effective_rebac_definition

_ModelT = TypeVar("_ModelT", bound=models.Model)


def _delete_rebac_resource_relationships(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Delete resource- and subject-side tuples after a concrete REBAC row is deleted."""

    del kwargs
    if not isinstance(instance, RebacMixin) or not model_resource_type(sender):
        return
    resource = to_object_ref(instance)
    delete_relationships(
        RelationshipFilter(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
        )
    )
    delete_relationships(
        RelationshipFilter(
            subject_type=resource.resource_type,
            subject_id=resource.resource_id,
        )
    )


def _bind_rebac_resource_relationship_gc(sender: type[models.Model], **kwargs: Any) -> None:
    """Bind tuple cleanup only to concrete models that inherit ``RebacMixin``."""

    del kwargs
    if sender._meta.abstract or not issubclass(sender, RebacMixin):
        return
    post_delete.connect(
        _delete_rebac_resource_relationships,
        sender=sender,
        dispatch_uid=f"angee.base.rebac_resource_relationship_gc.{sender._meta.label_lower}",
    )


class_prepared.connect(
    _bind_rebac_resource_relationship_gc,
    dispatch_uid="angee.base.bind_rebac_resource_relationship_gc",
)

CATALOGUE_TIERS = ("master", "install", "demo")
"""Resource tiers a catalogue model may declare.

Mirrors :class:`angee.resources.tiers.ResourceTier`, the authoritative resource
tier owner. ``angee.base`` cannot import the resources addon without reversing the
dependency direction, so the resources test suite pins these literals in sync.
"""


@dataclass(frozen=True, slots=True)
class DirectRecordAccess:
    """One direct declared-relation tuple on a shareable record."""

    relation: str
    subject: SubjectRef


EXTENSION_DONOR_STRUCTURAL_MEMBERS = frozenset(
    {
        "__annotations__",
        "__classcell__",
        "__dict__",
        "__doc__",
        "__firstlineno__",
        "__module__",
        "__qualname__",
        "__static_attributes__",
        "__weakref__",
        "DoesNotExist",
        "Meta",
        "MultipleObjectsReturned",
        "_meta",
        "extends",
        "id",
        "objects",
    }
)
"""Class-dict members that do not make a same-row extension donor semantic."""


class _PublicIdQuerySetMixin(Generic[_ModelT]):
    model: type[_ModelT]

    def from_public_id(self, value: str) -> _ModelT | None:
        """Return the row addressed by ``value`` within this queryset policy."""

        if value == "":
            return None
        try:
            lookup = cast(Any, self.model).public_id_lookup(value)
            return cast(_ModelT | None, cast(Any, self).filter(**lookup).first())
        except TypeError, ValueError:
            return None


class AngeeQuerySet(_PublicIdQuerySetMixin[_ModelT], RebacQuerySet[_ModelT]):
    """QuerySet API shared by Angee source and runtime models."""

    def lock_if_supported(self, *, of: tuple[str, ...] = ("self",)) -> Self:
        """Apply a self-scoped row lock only on database backends that support it."""

        features = connections[self.db].features
        if features.has_select_for_update:
            if of and features.has_select_for_update_of:
                return cast(Self, self.select_for_update(of=of))
            return cast(Self, self.select_for_update())
        return self

    def locked_get(self, *args: Any, **kwargs: Any) -> _ModelT:
        """Return one row under a database row lock when the backend supports it."""

        return self.lock_if_supported().get(*args, **kwargs)


class AngeeUnscopedQuerySet(_PublicIdQuerySetMixin[_ModelT], models.QuerySet[_ModelT]):
    """Angee queryset API for models that intentionally have no REBAC row policy."""

    def scoped_for_aggregate(self) -> Self:
        """Return this queryset for permission-naive aggregation.

        These querysets are only for Angee models without ``rebac_resource_type``;
        row authorization has no model-owned policy to apply.
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

        The REBAC pre-save signal cannot evaluate a per-row ``create`` gate
        for a row that has no id yet, so manager factories preflight the
        schema's ``create`` permission with the relations the row would
        carry (``rebac.check_new``), run the insert under per-instance
        sudo, and re-bind the verified actor on the saved row with
        ``with_actor`` so the bypass ends with that one insert.

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

    child_overrides_parent: bool = False
    """Whether a materialized child's own methods override its concrete parent's.

    A materialized child (``runtime = True`` + ``extends``) is emitted
    ``class Child(ConcreteParent, AbstractChild)`` — concrete parent first — so the
    parent wins the MRO and the child cannot override the parent's methods
    natively. Declaring ``child_overrides_parent = True`` flips this one child's
    base order to ``class Child(AbstractChild, ConcreteParent)`` so the child's own
    methods win. Read non-inherited (like ``runtime``); the default preserves the
    safe parent-first status quo, so ``parties.Person``/``Organization`` (which
    declare a different default manager than ``Party``) stay parent-first and
    byte-for-byte unchanged. The composer enforces the flip's manager/transition
    guards (see ``angee.compose.runtime``).
    """

    catalogue: bool = False
    """Whether this class declares itself as catalogue/reference data.

    The read is non-inherited: a subclass must declare ``catalogue = True`` on
    its own class body to opt in, matching ``runtime``'s structural-marker shape.
    """

    catalogue_tier: str = CATALOGUE_TIERS[0]
    """Resource tier the catalogue rows belong to; read non-inherited."""

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

    @classmethod
    def system_queryset(
        cls,
        *,
        using: str | None = None,
        lock: tuple[str, ...] | None = None,
    ) -> AngeeQuerySet[Self]:
        """Return an elevated unscoped queryset with backend-gated locks; SQLite stays unlocked.

        Locking was previously dead at these call sites and is now real.
        """

        queryset = AngeeQuerySet[Self](model=cls, using=using).system_context(
            reason=f"{cls._meta.label_lower}.system_queryset"
        )
        return queryset.lock_if_supported(of=lock) if lock is not None else queryset

    @classmethod
    def is_runtime_model(cls) -> bool:
        """Return whether this model class declares itself as a runtime model."""

        return bool(cls.__dict__.get("runtime", False))

    @classmethod
    def overrides_runtime_parent(cls) -> bool:
        """Return whether this materialized child opts into child-first emission."""

        return bool(cls.__dict__.get("child_overrides_parent", False))

    @classmethod
    def is_catalogue_model(cls) -> bool:
        """Return whether this class declares itself as catalogue data."""

        return bool(cls.__dict__.get("catalogue", False))

    @classmethod
    def get_catalogue_tier(cls) -> str:
        """Return this class's declared catalogue tier, defaulting to master."""

        return str(cls.__dict__.get("catalogue_tier", CATALOGUE_TIERS[0]))

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

        self._grant_declared_record_access(type(self), relation, subject)

    def _grant_declared_record_access(
        self,
        declaration_owner: type[AngeeModel],
        relation: str,
        subject: models.Model | SubjectRef,
    ) -> None:
        """Grant through one model class's own record-share declaration."""

        permission = declaration_owner.record_access_permission(relation)
        self._require_record_access(permission)
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

        self._revoke_declared_record_access(type(self), relation, subject)

    def _revoke_declared_record_access(
        self,
        declaration_owner: type[AngeeModel],
        relation: str,
        subject: models.Model | SubjectRef,
    ) -> None:
        """Revoke through one model class's own record-share declaration."""

        permission = declaration_owner.record_access_permission(relation)
        self._require_record_access(permission)
        delete_relationship(
            RelationshipTuple(
                resource=to_object_ref(self),
                relation=relation,
                subject=_relationship_subject(subject),
            )
        )

    def direct_record_access(self) -> tuple[DirectRecordAccess, ...]:
        """Return authorized direct tuples for this record's declared relations.

        This deliberately reads only stored relationship rows. It does not walk
        usersets, groups, roles, relation arrows, or effective permissions.
        """

        declaration = type(self).get_rebac_grantable()
        if not declaration:
            raise ValueError(f"{type(self)._meta.label} declares no grantable relations.")
        for permission in sorted(set(declaration.values())):
            self._require_record_access(permission)

        resource = to_object_ref(self)
        rows = (
            active_relationship_model()
            .objects.filter(
                resource_type=resource.resource_type,
                resource_id=resource.resource_id,
                relation__in=tuple(sorted(declaration)),
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

    def _require_record_access(self, permission: str) -> None:
        """Raise when the ambient actor lacks a declared share permission."""

        if not self.has_access(permission):
            raise PermissionDenied(f"Denied: the current actor lacks {permission!r} on {to_object_ref(self)}.")

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Run Django model checks plus Angee structural declaration checks."""

        errors = super().check(**kwargs)
        errors.extend(cls._check_catalogue_tier())
        errors.extend(cls._check_rebac_grantable())
        return errors

    @classmethod
    def _check_catalogue_tier(cls) -> list[checks.CheckMessage]:
        """Return system-check errors for an invalid catalogue tier declaration."""

        if not cls.is_catalogue_model():
            return []
        tier = cls.get_catalogue_tier()
        if tier in CATALOGUE_TIERS:
            return []
        expected = ", ".join(repr(value) for value in CATALOGUE_TIERS)
        return [
            checks.Error(
                f"{cls._meta.label}.catalogue_tier must be one of {expected}; got {tier!r}.",
                obj=cls,
                id="angee.E014",
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
                        f"must store direct tuples, not use {relation.backing.kind!r} backing.",
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

    def apply_create_defaults(self) -> Mapping[str, Sequence[Any]]:
        """Apply this row's blank-on-input create defaults before the create gate.

        The auto-CRUD create preflight (``AngeeManager.check_create`` via the
        Hasura write backend) evaluates the REBAC ``create`` permission against
        the unsaved instance *before* ``save()`` runs. A field a model defaults
        in ``save()`` — a blank-on-input scope relation derived from the actor,
        for example — is therefore still blank when the gate fires, so a
        ``create = scope->member`` arm fail-closes on a create that would in fact
        have persisted a scope.

        A model that defaults a subject-bearing relation on ``save()`` overrides
        this hook to apply that default here too (idempotent with ``save()``, so
        the row still persists with it) and return the relation contributions the
        default adds, keyed by relation name with subject values — so the gate is
        evaluated against the row as it will persist. The base default applies no
        defaults and contributes nothing.
        """

        return {}

    @classmethod
    def get_extension_target(cls) -> str | None:
        """Return the normalized model label this source model extends."""

        target = cls.extends
        if target is None:
            return None
        if not isinstance(target, str):
            raise ImproperlyConfigured(f"{cls.__module__}.{cls.__name__}.extends must be a string.")
        try:
            app_label, model_name = make_model_tuple(target)
        except ValueError as error:
            raise ImproperlyConfigured(
                f"{cls.__module__}.{cls.__name__}.extends must be an 'app_label.ModelName' reference."
            ) from error
        return f"{app_label}.{model_name}"

    @classmethod
    def get_extension_bases(cls) -> tuple[type[models.Model], ...]:
        """Return abstract model bases contributed by this extension."""

        if cls.get_extension_target() is None:
            return ()

        bases = tuple(base for base in cls.__bases__ if _is_contributed_extension_base(base))
        if bases and cls._has_extension_body():
            cls._check_bodyful_extension_bases(bases)
            return (cls, *bases)
        return bases or (cls,)

    @classmethod
    def _has_extension_body(cls) -> bool:
        """Return whether this donor declares semantic members of its own."""

        return any(
            name not in EXTENSION_DONOR_STRUCTURAL_MEMBERS and not _is_inherited_abstract_field_member(cls, name)
            for name in cls.__dict__
        )

    @classmethod
    def _check_bodyful_extension_bases(cls, bases: tuple[type[models.Model], ...]) -> None:
        """Raise if this donor cannot be composed ahead of its contributed bases."""

        donor = f"{cls.__module__}.{cls.__name__}"
        if not cls._meta.abstract:
            raise ImproperlyConfigured(f"{donor} cannot compose as a same-row extension because it is not abstract.")
        seen: set[type[models.Model]] = set()
        for base in bases:
            if base in seen:
                raise ImproperlyConfigured(
                    f"{donor} cannot compose duplicate extension base {base.__module__}.{base.__name__}."
                )
            seen.add(base)
            if not base._meta.abstract:
                raise ImproperlyConfigured(
                    f"{donor} cannot compose non-abstract extension base {base.__module__}.{base.__name__}."
                )
            if not issubclass(cls, base):
                raise ImproperlyConfigured(
                    f"{donor} cannot compose its own body ahead of {base.__module__}.{base.__name__}; "
                    "the donor must inherit every contributed base."
                )

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
    anchor at emission (``Runtime._class_import``) and fails loudly on a mis-capture
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


def _is_contributed_extension_base(value: type) -> bool:
    """Return whether ``value`` is an abstract model extension base."""

    if not issubclass(value, models.Model):
        return False
    if value in {models.Model, TimestampMixin, RebacMixin, AngeeModel, AngeeDataModel}:
        return False
    model = cast(type[models.Model], value)
    meta = model._meta
    return bool(meta.abstract)


def _is_inherited_abstract_field_member(model: type[models.Model], name: str) -> bool:
    """Return whether ``name`` is Django plumbing copied from an abstract base field."""

    for base in model.__mro__[1:]:
        if base is models.Model or not issubclass(base, models.Model) or not hasattr(base, "_meta"):
            continue
        abstract_base = cast(type[models.Model], base)
        if abstract_base._meta.abstract and name in _abstract_field_member_names(abstract_base):
            return True
    return False


def _abstract_field_member_names(model: type[models.Model]) -> frozenset[str]:
    """Return class-dict names Django contributes for ``model``'s local fields."""

    names: set[str] = set()
    for field in (*model._meta.local_fields, *model._meta.local_many_to_many):
        names.add(field.name)
        attname = getattr(field, "attname", None)
        if isinstance(attname, str):
            names.add(attname)
        if getattr(field, "choices", None):
            names.add(f"get_{field.name}_display")
        if isinstance(field, models.DateField) and not field.null:
            names.add(f"get_next_by_{field.name}")
            names.add(f"get_previous_by_{field.name}")
    return frozenset(names)
