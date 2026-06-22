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
from rebac.errors import MissingActorError, PermissionDenied
from rebac.managers import RebacManager, RebacQuerySet
from rebac.resources import model_resource_type
from sqids import Sqids

from angee.base.fields import SqidField
from angee.base.mixins import SqidMixin, TimestampMixin

_ModelT = TypeVar("_ModelT", bound=models.Model)


class AngeeQuerySet(RebacQuerySet[_ModelT]):
    """QuerySet API shared by Angee source and runtime models."""

    def from_public_id(self, value: str) -> _ModelT | None:
        """Return the row addressed by ``value`` within this queryset policy."""

        return _instance_from_public_id_queryset(self, value)

    async def afrom_public_id(self, value: str) -> _ModelT | None:
        """Async :meth:`from_public_id` — resolves on the event loop, row-scoped."""

        return await _ainstance_from_public_id_queryset(self, value)

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
            actor = queryset._resolve_effective_actor()[0]
        except MissingActorError:
            return cast(Self, queryset.none())
        if actor is None and model_resource_type(self.model):
            return cast(Self, queryset.none())
        return queryset.apply_ambient_scope()


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
    def get_composition_label(cls) -> str:
        """Return this model's normalized composition label."""

        return cls._meta.label_lower

    @classmethod
    def is_runtime_model(cls) -> bool:
        """Return whether this model class declares itself as a runtime model."""

        return cls.__dict__.get("runtime", False)

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
    model_label: str | None = None
    public_id_field: str = "sqid"
    min_length: int = 8
    alphabet: str | None = None

    def public_id_from_pk(self, value: Any) -> str:
        """Return the public id encoded from a primary-key value."""

        if value in (None, ""):
            return ""
        encoded_value = self._codec().encode([int(value)])
        return f"{self.canonical_prefix}{encoded_value}" if encoded_value is not None else ""

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

        return SqidField._canonical_prefix(self.prefix)

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


def public_data_id_prefix(model: type[models.Model]) -> str:
    """Return the public sqid prefix declared by ``model``."""

    field = public_data_id_field(model)
    if field is not None:
        return field.prefix
    return ""


def public_data_id_owner(model: type[models.Model]) -> type[models.Model] | None:
    """Return the model class that owns ``model``'s public data identity."""

    field = public_data_id_field(model)
    if field is not None:
        return cast(type[models.Model], field.model)
    return None


def is_public_data_model(model: type[models.Model]) -> bool:
    """Return whether ``model`` exposes Angee's public data identity contract."""

    return public_data_id_owner(model) is not None


def public_id_value_of(instance: models.Model) -> Any:
    """Return the model-owned public id value for ``instance`` if available."""

    resolver = getattr(instance, "public_id_value", None)
    if callable(resolver):
        return resolver()
    return instance.pk


def instance_from_public_id(
    model: type[_ModelT],
    value: str,
    *,
    queryset: models.QuerySet[_ModelT] | None = None,
    public_identity: SqidPublicIdentity | None = None,
) -> _ModelT | None:
    """Return ``model`` instance addressed by Angee or Django public ID."""

    active_queryset = queryset if queryset is not None else model._default_manager.all()
    resolver = getattr(active_queryset, "from_public_id", None)
    if callable(resolver):
        return cast(_ModelT | None, resolver(value))

    return _instance_from_public_id_queryset(
        active_queryset,
        value,
        public_identity=public_identity,
    )


def public_id_of(instance: models.Model) -> str:
    """Return the Angee public ID or Django primary key for ``instance``."""

    value = public_id_value_of(instance)
    if value in (None, ""):
        return ""
    return str(value)


def public_id_for(
    model: type[models.Model],
    pk: Any,
    *,
    public_identity: SqidPublicIdentity | None = None,
) -> str:
    """Return the public id for ``model`` when only its primary key is known."""

    if pk in (None, ""):
        return ""
    if public_identity is not None:
        return public_identity.public_id_from_pk(pk)
    resolver = getattr(model, "public_id_from_pk", None)
    if callable(resolver):
        return str(resolver(pk))
    return str(pk)


def _relationship_subject(value: Any) -> SubjectRef:
    """Return one preflight relationship value as a REBAC subject reference."""

    if isinstance(value, SubjectRef):
        return value
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
    except TypeError, ValueError:
        return None
    return cast(_ModelT | None, instance)


async def _ainstance_from_public_id_queryset(
    queryset: models.QuerySet[_ModelT],
    value: str,
) -> _ModelT | None:
    """Async mirror of :func:`_instance_from_public_id_queryset`."""

    if value == "":
        return None
    try:
        instance = await queryset.filter(**_public_id_lookup(queryset.model, value)).afirst()
    except TypeError, ValueError:
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


def _is_contributed_extension_base(value: type) -> bool:
    """Return whether ``value`` is an abstract model extension base."""

    if not issubclass(value, models.Model):
        return False
    if value in {models.Model, TimestampMixin, RebacMixin, AngeeModel, AngeeDataModel}:
        return False
    model = cast(type[models.Model], value)
    meta = model._meta
    return bool(meta.abstract)
