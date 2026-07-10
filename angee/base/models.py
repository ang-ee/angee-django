"""Runtime model primitives shared by composed Angee applications."""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self, TypeVar, cast

from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import connections, models
from django.db.models.utils import make_model_tuple
from rebac import RebacMixin, SubjectRef, check_new, current_actor, to_object_ref
from rebac.actors import is_sudo as ambient_is_sudo
from rebac.actors import to_subject_ref
from rebac.errors import MissingActorError, NoActorResolvedError, PermissionDenied
from rebac.managers import RebacManager, RebacQuerySet
from rebac.resources import model_resource_type

from angee.base.fields import SqidField
from angee.base.impl import ImplClassField
from angee.base.mixins import SqidMixin, TimestampMixin

_ModelT = TypeVar("_ModelT", bound=models.Model)

CATALOGUE_TIERS = ("master", "install", "demo")
"""Resource tiers a catalogue model may declare.

Mirrors :class:`angee.resources.tiers.ResourceTier`, the authoritative resource
tier owner. ``angee.base`` cannot import the resources addon without reversing the
dependency direction, so the resources test suite pins these literals in sync.
"""


class AngeeQuerySet(RebacQuerySet[_ModelT]):
    """QuerySet API shared by Angee source and runtime models."""

    def from_public_id(self, value: str) -> _ModelT | None:
        """Return the row addressed by ``value`` within this queryset policy."""

        return _instance_from_public_id_queryset(self, value)

    def apply_ambient_scope(self) -> Self:
        """Eagerly apply REBAC row scope using the queryset or ambient actor."""

        self._apply_scope_in_place()
        return self

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

    def scoped_for_aggregate(self) -> Self:
        """Return a row-scoped queryset safe for permission-naive aggregation.

        Aggregate compilers run through ``.values()``/``.aggregate()`` shapes
        whose dict rows field-read redaction cannot touch, so field redaction is
        disabled and REBAC row scope is applied eagerly. It fails closed: a
        REBAC-typed model with no actor and no sudo bypass returns an empty
        queryset rather than leaking every row, independent of
        ``REBAC_STRICT_MODE``. An explicit sudo — per-queryset ``.sudo()`` or an
        ambient ``system_context`` — aggregates across all rows, unscoped, by
        design.
        """

        queryset = cast(Self, self.on_field_deny("allow"))
        if queryset.is_sudo() or ambient_is_sudo():
            return queryset
        actor = queryset.actor() or current_actor()
        if actor is None:
            if model_resource_type(self.model):
                return cast(Self, queryset.none())
            return queryset
        if not model_resource_type(self.model):
            return queryset
        return queryset.apply_ambient_scope()


class AngeeUnscopedQuerySet(models.QuerySet[_ModelT]):
    """Angee queryset API for models that intentionally have no REBAC row policy."""

    def from_public_id(self, value: str) -> _ModelT | None:
        """Return the row addressed by ``value`` within this queryset."""

        return _instance_from_public_id_queryset(self, value)

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

    class Meta:
        """Django model options for Angee's abstract model base."""

        abstract = True

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
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Run Django model checks plus Angee structural declaration checks."""

        errors = super().check(**kwargs)
        errors.extend(cls._check_catalogue_tier())
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
        in ``save()`` — an :class:`~angee.iam.models.CompanyScopedMixin`
        ``company`` taken from the actor's sole membership — is therefore still
        blank when the gate fires, so a ``create = company->member`` arm
        fail-closes on a create that would in fact have persisted a company.

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
        return bases or (cls,)

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


@dataclass(frozen=True, slots=True)
class SqidPublicIdentity:
    """Sqid adapter for a model Angee does not own with a ``SqidField``.

    Survives only for third-party Django models such as ``auth.Group`` where
    Angee exposes a public sqid-shaped surface but cannot add its own field.
    ``SqidField`` still owns the codec and prefix rules.
    """

    prefix: str
    min_length: int | None = None
    alphabet: str | None = None

    def public_id_from_pk(self, value: Any) -> str:
        """Return the public id encoded from a primary-key value."""

        return self.sqid_field.public_id_from_value(value)

    def public_id_to_pk(self, value: str) -> int | None:
        """Decode one public id to the backing primary-key value."""

        return self.sqid_field.public_id_to_value(value)

    def public_id_lookup(self, model: type[models.Model], value: str) -> dict[str, Any]:
        """Return a Django lookup for ``value`` against ``model``."""

        pk = model._meta.pk
        return {pk.name: self.public_id_to_pk(value)} if pk is not None else {}

    @property
    def sqid_field(self) -> SqidField:
        """Return the owner field used to encode and decode this adapter's ids."""

        # Deliberately per-call: this rare third-party path keeps SqidField as
        # the codec owner without attaching a field to a model Angee does not own.
        return SqidField(
            real_field_name="id",
            prefix=self.prefix,
            min_length=self.min_length,
            alphabet=self.alphabet,
        )


def public_data_id_field(model: type[models.Model]) -> SqidField | None:
    """Return the sqid field that makes ``model`` safe for public data surfaces."""

    for owner in (model, *model._meta.get_parent_list()):
        try:
            field = owner._meta.get_field("sqid")
        except FieldDoesNotExist:
            continue
        if isinstance(field, SqidField):
            return field
    return None


def instance_from_public_id(
    model: type[_ModelT],
    value: str,
    *,
    queryset: models.QuerySet[_ModelT] | None = None,
    public_identity: SqidPublicIdentity | None = None,
) -> _ModelT | None:
    """Return ``model`` instance addressed by a public id.

    Thin adapter for generic surfaces that receive a model class/queryset at
    runtime or a third-party ``public_identity``; Angee-owned callers should use
    ``Model.from_public_id`` or ``queryset.from_public_id`` when they know the
    owner statically.
    """

    active_queryset = queryset if queryset is not None else model._default_manager.all()
    return _instance_from_public_id_queryset(
        active_queryset,
        value,
        public_identity=public_identity,
    )


def public_id_of(instance: models.Model) -> str:
    """Return the public id for a generic model instance.

    Thin adapter for generic surfaces that may receive a third-party Django
    model. Angee-owned instances should use their ``public_id`` property.
    """

    public_id = getattr(instance, "public_id", None)
    if isinstance(public_id, str):
        return public_id
    pk = instance.pk
    if pk in (None, ""):
        return ""
    return str(pk)


def public_id_for(
    model: type[models.Model],
    pk: Any,
    *,
    public_identity: SqidPublicIdentity | None = None,
) -> str:
    """Return the public id for a generic model when only its primary key is known.

    Thin adapter for relation/projector code that receives a model class at
    runtime, including third-party models reached through ``public_identity``.
    Angee-owned callers should prefer ``Model.public_id_from_pk``.
    """

    if pk in (None, ""):
        return ""
    if public_identity is not None:
        return public_identity.public_id_from_pk(pk)
    resolver = getattr(model, "public_id_from_pk", None)
    if callable(resolver):
        return str(resolver(pk))
    return str(pk)


def bind_actor(instance: models.Model, actor: Any | None) -> None:
    """Bind ``actor`` to ``instance`` when the model owns REBAC row policy."""

    if actor is None:
        return
    with_actor = getattr(instance, "with_actor", None)
    if callable(with_actor):
        with_actor(actor)
        return
    if requires_angee_rebac_contract(type(instance)):
        raise ImproperlyConfigured(f"{instance._meta.label} instances must expose with_actor(actor).")


def aggregate_scoped_queryset(queryset: models.QuerySet[_ModelT]) -> models.QuerySet[_ModelT]:
    """Return the aggregate-safe scoped queryset for a REBAC model."""

    scoped = getattr(queryset, "scoped_for_aggregate", None)
    if callable(scoped):
        return cast(models.QuerySet[_ModelT], scoped())
    if requires_angee_rebac_contract(queryset.model):
        raise ImproperlyConfigured(f"{queryset.model._meta.label} querysets must expose scoped_for_aggregate().")
    return queryset


def read_scoped_queryset(
    model: type[_ModelT],
    actor: Any | None,
    *,
    action: str = "read",
) -> models.QuerySet[_ModelT] | None:
    """Return a queryset scoped to ``actor`` for models with a REBAC row policy."""

    if not model_resource_type(model) or actor is None:
        return None
    manager = model._default_manager
    with_actor = getattr(manager, "with_actor", None)
    if not callable(with_actor):
        if requires_angee_rebac_contract(model):
            raise ImproperlyConfigured(f"{model._meta.label} manager must expose with_actor(actor).")
        return None
    queryset = with_actor(actor)
    with_action = getattr(queryset, "with_action", None)
    if callable(with_action):
        queryset = with_action(action)
    elif requires_angee_rebac_contract(model):
        raise ImproperlyConfigured(f"{model._meta.label} querysets must expose with_action(action).")
    return cast(models.QuerySet[_ModelT], queryset)


def write_scoped_queryset(model: type[_ModelT]) -> models.QuerySet[_ModelT]:
    """Return a write-target queryset with REBAC row scope and unredacted fields."""

    manager = model._default_manager
    for_write = getattr(manager, "for_write", None)
    if callable(for_write):
        return cast(models.QuerySet[_ModelT], for_write())
    if requires_angee_rebac_contract(model):
        raise ImproperlyConfigured(f"{model._meta.label} manager must expose for_write().")
    return manager.all()


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


def _instance_from_public_id_queryset(
    queryset: models.QuerySet[_ModelT],
    value: str,
    *,
    public_identity: SqidPublicIdentity | None = None,
) -> _ModelT | None:
    """Return the row addressed by ``value`` using ``queryset`` as the owner."""

    if value == "":
        return None

    try:
        if public_identity is not None:
            lookup = public_identity.public_id_lookup(queryset.model, value)
        else:
            lookup_owner = getattr(queryset.model, "public_id_lookup", None)
            if callable(lookup_owner):
                lookup = dict(lookup_owner(value))
            else:
                pk = queryset.model._meta.pk
                lookup = {pk.name: value} if pk is not None else {}
        instance = queryset.filter(**lookup).first()
    except (TypeError, ValueError):
        return None
    return cast(_ModelT | None, instance)


def requires_angee_rebac_contract(model: type[models.Model]) -> bool:
    """Return whether ``model`` is an Angee model with declared row authorization."""

    return issubclass(model, AngeeModel) and bool(model_resource_type(model))


def _is_contributed_extension_base(value: type) -> bool:
    """Return whether ``value`` is an abstract model extension base."""

    if not issubclass(value, models.Model):
        return False
    if value in {models.Model, TimestampMixin, RebacMixin, AngeeModel, AngeeDataModel}:
        return False
    model = cast(type[models.Model], value)
    meta = model._meta
    return bool(meta.abstract)
