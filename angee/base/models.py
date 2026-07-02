"""Runtime model primitives shared by composed Angee applications."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self, TypeVar, cast

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from django.db.models.utils import make_model_tuple
from django_sqids.field import DEFAULT_ALPHABET
from rebac import RebacMixin, SubjectRef, check_new, current_actor, to_object_ref
from rebac.actors import is_sudo as ambient_is_sudo
from rebac.actors import to_subject_ref
from rebac.errors import MissingActorError, NoActorResolvedError, PermissionDenied
from rebac.managers import RebacManager, RebacQuerySet
from rebac.resources import model_resource_type
from sqids import Sqids

from angee.base.fields import ImplClassField, SqidField, canonical_sqid_prefix, encode_public_id
from angee.base.mixins import SqidMixin, TimestampMixin

_ModelT = TypeVar("_ModelT", bound=models.Model)


class AngeeQuerySet(RebacQuerySet[_ModelT]):
    """QuerySet API shared by Angee source and runtime models."""

    def from_public_id(self, value: str) -> _ModelT | None:
        """Return the row addressed by ``value`` within this queryset policy."""

        return _instance_from_public_id_queryset(self, value)

    def apply_ambient_scope(self) -> Self:
        """Eagerly apply REBAC row scope using the queryset or ambient actor."""

        self._apply_scope_in_place()
        return self

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
        try:
            actor, is_unscoped = queryset.effective_actor(strict=False)
        except MissingActorError:
            return cast(Self, queryset.none())
        if is_unscoped:
            return queryset
        if actor is None and model_resource_type(self.model):
            return cast(Self, queryset.none())
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

    class Meta:
        """Django model options for Angee's abstract model base."""

        abstract = True

    @classmethod
    def is_runtime_model(cls) -> bool:
        """Return whether this model class declares itself as a runtime model."""

        return cls.__dict__.get("runtime", False)

    @classmethod
    def impl_key_for(cls, field_name: str, value: Any, *, default: str | None = None) -> str:
        """Return the canonical registry key for one ``ImplClassField`` value."""

        field = cls._impl_field(field_name)
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

        field = cls._impl_field(field_name)
        key = cls.impl_key_for(field_name, value, default=default)
        return field.resolve_class(key)

    @classmethod
    def _impl_field(cls, field_name: str) -> Any:
        """Return the named impl field or raise when the field is not impl-owned."""

        field = cls._meta.get_field(field_name)
        if not isinstance(field, ImplClassField):
            raise FieldDoesNotExist(f"{cls.__name__}.{field_name} is not an ImplClassField.")
        return field

    def resolve_impl(self, field_name: str, *, default: str | None = None) -> type:
        """Return the impl class selected by ``field_name`` on this instance."""

        field = type(self)._impl_field(field_name)
        value = getattr(self, field.attname)
        if not value and default is not None:
            return field.resolve_class(default)
        return field.resolve_for(self)

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


class AngeeDataModel(SqidMixin, AngeeModel):
    """Abstract base for Angee rows that participate in public data contracts."""

    class Meta:
        """Django model options for Angee's public data model base."""

        abstract = True


@dataclass(frozen=True, slots=True)
class SqidPublicIdentity:
    """Sqid public identity for a model Angee does not own with a field."""

    prefix: str
    min_length: int = 8
    alphabet: str | None = None

    def public_id_from_pk(self, value: Any) -> str:
        """Return the public id encoded from a primary-key value."""

        return encode_public_id(self._codec(), self.canonical_prefix, value)

    def public_id_to_pk(self, value: str) -> int | None:
        """Decode one public id to the backing primary-key value."""

        raw_value = value
        if self.canonical_prefix:
            if not value.startswith(self.canonical_prefix):
                return None
            raw_value = value[len(self.canonical_prefix) :]
        decoded = self._codec().decode(raw_value)
        return decoded[0] if len(decoded) == 1 else None

    def public_id_lookup(self, model: type[models.Model], value: str) -> dict[str, Any]:
        """Return a Django lookup for ``value`` against ``model``."""

        pk = model._meta.pk
        return {pk.name: self.public_id_to_pk(value)} if pk is not None else {}

    @property
    def canonical_prefix(self) -> str:
        """Return the canonical Angee sqid prefix."""

        return canonical_sqid_prefix(self.prefix)

    def _codec(self) -> Sqids:
        """Return the sqids codec for this identity."""

        alphabet = self.alphabet or getattr(settings, "DJANGO_SQIDS_ALPHABET", None) or DEFAULT_ALPHABET
        return Sqids(min_length=self.min_length, alphabet=alphabet)


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
    """Return ``model`` instance addressed by Angee or Django public ID."""

    active_queryset = queryset if queryset is not None else model._default_manager.all()
    return _instance_from_public_id_queryset(
        active_queryset,
        value,
        public_identity=public_identity,
    )


def public_id_of(instance: models.Model) -> str:
    """Return the Angee public id or Django primary key for ``instance``.

    The user model is swappable, so ``instance`` may be a plain Django model
    (e.g. ``django.contrib.auth.User``) that Angee does not own — those carry no
    ``public_id_value`` and fall back to the primary key.
    """

    resolver = getattr(instance, "public_id_value", None)
    value = resolver() if callable(resolver) else instance.pk
    if value in (None, ""):
        return ""
    return str(value)


def public_id_for(
    model: type[models.Model],
    pk: Any,
    *,
    public_identity: SqidPublicIdentity | None = None,
) -> str:
    """Return the public id for ``model`` when only its primary key is known.

    ``model`` may be a plain Django model Angee does not own (the swappable user
    model, or a third-party model reached with a ``public_identity`` decoder);
    one without the public-id contract falls back to its primary key.
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
        lookup = _public_id_lookup(queryset.model, value, public_identity=public_identity)
        instance = queryset.filter(**lookup).first()
    except (TypeError, ValueError):
        return None
    return cast(_ModelT | None, instance)


def _public_id_lookup(
    model: type[models.Model],
    value: str,
    *,
    public_identity: SqidPublicIdentity | None = None,
) -> dict[str, Any]:
    """Return the model-owned lookup for one public id value."""

    if public_identity is not None:
        return public_identity.public_id_lookup(model, value)
    lookup = getattr(model, "public_id_lookup", None)
    if callable(lookup):
        return dict(lookup(value))
    pk = model._meta.pk
    return {pk.name: value} if pk is not None else {}


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
