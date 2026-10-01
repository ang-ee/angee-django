"""Generic record references backed by Django contenttypes.

Also the owner of the **record-target-across-MTI policy**: a polymorphic edge and a
REBAC grant see a multi-table-inheritance row from different sides.
:func:`canonical_record_model` and :func:`canonical_record_target` own the write
identity; :func:`ancestor_object_refs` owns the read/grant fan-out.

**Placement invariant.** A polymorphic edge that keys on
:func:`canonical_record_target` — ``storage.FileAttachment``, ``tags.TagAssignment``,
``messaging.ThreadAttachment``, ``knowledge.RecordBinding``, and every reverse
``GenericRelation`` onto such an edge (``messaging.ThreadedModelMixin.thread_attachments``,
a future ``tags`` relation on ``Party``) — must be declared on, and any mixin owning it
composed onto, the *same* topmost REBAC-typed MTI ancestor the canonical write keys on. A reverse
``GenericRelation`` filters at its declaring model's own content type, so composing the
mixin on a child while its canonical ancestor does not splits the write content type
from the collect content type and orphans edge rows on delete.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, NamedTuple

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from rebac import ObjectRef, to_object_ref
from rebac.resources import model_resource_type

from angee.base.identity import public_data_id_field, public_id_for


@dataclass(frozen=True, slots=True)
class RecordRef:
    """Frozen public identity for a model row reached through a generic pointer."""

    model_label: str
    object_id: Any | None
    public_id: str
    resource_type: str


def record_ref_for(instance: models.Model) -> RecordRef:
    """Return the stable public reference for ``instance``."""

    model = type(instance)
    return _record_ref_from_model(model, instance.pk)


class CanonicalRecordTarget(NamedTuple):
    """The content type and id a polymorphic edge must store for a target row."""

    content_type: ContentType
    object_id: Any


def canonical_record_target(obj: models.Model) -> CanonicalRecordTarget:
    """Return the content type and id a polymorphic edge must store for ``obj``.

    The **write rule** for a generic foreign key across multi-table inheritance:
    resolve ``obj`` to its concrete model first (unwrapping any proxy), then
    canonicalize to the *topmost* concrete MTI ancestor that declares a
    ``rebac_resource_type`` — a ``parties.Person`` row canonicalizes to its
    ``parties.Party`` ancestor — so a child and its parent share one edge set instead
    of splitting it across their two content types. Resolving the proxy first means an
    untyped proxy over a typed concrete row keys on the typed concrete ancestor, never
    the proxy's own content type: a proxy is a presentation of its concrete row, not a
    distinct target (this replaces the earlier "keep the proxy's own content type"
    behavior). A row with no REBAC-typed ancestor keys on its concrete content type.
    MTI shares one primary key down the pk-link chain, so ``obj.pk`` addresses the row
    at whichever ancestor owns the edge; :func:`ancestor_object_refs` is the dual that
    reads every level back.
    """

    model = canonical_record_model(type(obj))
    return CanonicalRecordTarget(ContentType.objects.get_for_model(model), obj.pk)


def ancestor_object_refs(obj: models.Model) -> tuple[ObjectRef, ...]:
    """Return every REBAC identity ``obj`` IS-A, nearest identity first.

    The **read/grant fan-out** dual of :func:`canonical_record_target`: ``obj``'s own
    identity first (raises :class:`TypeError` if its model declares no
    ``rebac_resource_type``), then each REBAC-registered concrete MTI ancestor it shares
    a primary key with (``parties.Person`` IS-A ``parties.Party``). Every identity shares
    ``obj``'s REBAC id, so a grant or read on any ancestor type reaches the same row —
    the reason a foreign key typed to a parent still scopes the child in. Returned
    eagerly as a tuple, so the fail-fast fires at the call rather than on first iteration.
    """

    own = to_object_ref(obj)
    refs = [own]
    seen = {own.resource_type}
    for ancestor in _pk_ancestor_chain(type(obj)._meta.concrete_model or type(obj)):
        resource_type = model_resource_type(ancestor)
        if resource_type is not None and resource_type not in seen:
            seen.add(resource_type)
            refs.append(ObjectRef(resource_type, own.resource_id))
    return tuple(refs)


def canonical_record_model(model: type[models.Model]) -> type[models.Model]:
    """Return the topmost concrete MTI ancestor of ``model`` with a REBAC type.

    This is the model-class projection of :func:`canonical_record_target`, for
    callers such as resource metadata that need the canonical label without an
    instance or a contenttypes query. Proxies unwrap first; untyped rows fall back
    to their concrete model.
    """

    concrete = model._meta.concrete_model or model
    typed = [c for c in _pk_ancestor_chain(concrete) if model_resource_type(c) is not None]
    return typed[-1] if typed else concrete


def is_record_target_model(model: type[models.Model]) -> bool:
    """Whether rows of ``model`` can be targets of a polymorphic record edge.

    Edges that name a record by canonical target (knowledge bindings, decision
    evidence) admit only records with a REBAC type, so only such models — and their
    MTI children and proxies — need delete-time care for those edges. A model with
    several concrete MTI parents has no canonical target, so it carries none.
    """

    try:
        return model_resource_type(canonical_record_model(model)) is not None
    except ValueError:
        return False


def concrete_child_models(parent_model: type[models.Model]) -> tuple[type[models.Model], ...]:
    """Return direct, installed MTI children in stable model-label order."""

    return tuple(
        sorted(
            (
                model
                for model in parent_model._meta.apps.get_models()
                if model._meta.managed and not model._meta.proxy and tuple(model._meta.parents) == (parent_model,)
            ),
            key=lambda model: model._meta.label_lower,
        )
    )


def concrete_child_accessor(parent_model: type[models.Model], child_model: type[models.Model]) -> str:
    """Return Django's reverse accessor for a direct MTI parent link."""

    parent_link = child_model._meta.parents.get(parent_model)
    if parent_link is None:
        raise ImproperlyConfigured(f"{child_model._meta.label} is not a direct child of {parent_model._meta.label}.")
    return str(parent_link.remote_field.get_accessor_name())


def concrete_child(
    parent: models.Model,
    child_model: type[models.Model],
    *,
    queryset: models.QuerySet[Any] | None = None,
    cache_attr: str | None = None,
) -> models.Model | None:
    """Read one child through a prefetch cache or its supplied base/scoped queryset.

    The caller owns access policy. The default base manager reads structural MTI
    identity; an actor-scoped queryset can instead restrict the returned row.
    """

    if queryset is None and isinstance(parent, child_model):
        return parent
    parent_model = next((model for model in child_model._meta.parents if isinstance(parent, model)), type(parent))
    accessor = concrete_child_accessor(parent_model, child_model)
    if cache_attr is not None and hasattr(parent, cache_attr):
        cached = getattr(parent, cache_attr)
        if isinstance(cached, (list, tuple)):
            return cached[0] if cached else None
        return cached
    if queryset is None and accessor in parent._state.fields_cache:
        return parent._state.fields_cache[accessor]
    rows = queryset if queryset is not None else child_model._base_manager.all()
    return rows.filter(pk=parent.pk).first()


def _pk_ancestor_chain(model: type[models.Model]) -> Iterator[type[models.Model]]:
    """Yield ``model`` then each concrete MTI ancestor it shares its primary key with.

    Follows only the single primary-key ``parent_link`` at each level. A secondary MTI
    parent keeps its own primary key, so ``model``'s pk does not address that row and
    fanning an edge or grant onto its content type would corrupt; more than one concrete
    parent path is that ambiguous multiple-MTI shape and fails fast.
    """

    current: type[models.Model] | None = model
    while current is not None:
        yield current
        parents = current._meta.parents
        if len(parents) > 1:
            raise ValueError(
                f"{current._meta.label} has more than one concrete parent; a canonical "
                "record target is defined only along a single primary-key MTI chain."
            )
        current = next(iter(parents), None)


class RecordRefMixin(models.Model):
    """Project a row reference from the model's single declared generic foreign key."""

    class Meta:
        """Django model options for record-ref-only abstract inheritance."""

        abstract = True

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Reject ambiguous generic pointers and the obsolete prefix declaration."""

        errors = super().check(**kwargs)
        references = [field for field in cls._meta.private_fields if isinstance(field, GenericForeignKey)]
        if len(references) != 1:
            errors.append(
                checks.Error(
                    f"{cls._meta.label} must declare exactly one GenericForeignKey for RecordRefMixin; "
                    f"found {len(references)}.",
                    obj=cls,
                    id="angee.E029",
                )
            )
        if hasattr(cls, "record_ref_field_prefix"):
            errors.append(
                checks.Error(
                    f"{cls._meta.label}.record_ref_field_prefix is obsolete; "
                    "the GenericForeignKey owns its field names.",
                    obj=cls,
                    id="angee.E030",
                )
            )
        return errors

    @property
    def record_ref(self) -> RecordRef:
        """Return this row's referenced record identity without loading the target."""

        reference = self.record_ref_field()
        content_type_id = getattr(self, reference.ct_field_attname)
        object_id = getattr(self, reference.fk_field)
        if content_type_id in (None, "") or object_id in (None, ""):
            return _empty_record_ref(object_id)
        model = ContentType.objects.get_for_id(content_type_id).model_class()
        if model is None:
            return _empty_record_ref(object_id)
        return _record_ref_from_model(model, object_id)

    @classmethod
    def record_ref_field(cls) -> GenericForeignKey:
        """Return the generic pointer that owns this model's record reference."""

        references = [field for field in cls._meta.private_fields if isinstance(field, GenericForeignKey)]
        if len(references) != 1:
            raise ImproperlyConfigured(f"{cls._meta.label} must declare exactly one GenericForeignKey.")
        return references[0]

    @classmethod
    def record_public_id_operand(cls, value: str) -> models.Case:
        """Decode a public ID to a SQL operand bound to the pointer's model.

        Generic references carry both a content type and an object ID. Decode
        through each target's existing identity field, retaining its content type
        in a native CASE expression so equal numeric keys on different models
        cannot alias. No target rows or content types are fetched to filter rows.
        """

        reference = cls.record_ref_field()
        candidates = []
        for model in sorted(cls._meta.apps.get_models(), key=lambda item: item._meta.label_lower):
            field = public_data_id_field(model)
            try:
                pk = field.public_id_to_value(value) if field else model._meta.pk.to_python(value)
                if pk is None or public_id_for(model, pk) != value:
                    continue
            except (TypeError, ValueError, ValidationError):
                continue
            candidates.append(models.When(
                **{
                    f"{reference.ct_field}__app_label": model._meta.app_label,
                    f"{reference.ct_field}__model": model._meta.model_name,
                },
                then=models.Value(str(pk)),
            ))
        return models.Case(*candidates, default=models.Value(""), output_field=models.CharField())

    @property
    def record_model_label(self) -> str:
        """Return the referenced record's ``app_label.ModelName`` label."""

        return self.record_ref.model_label

    @property
    def record_public_id(self) -> str:
        """Return the referenced record's stable public id."""

        return self.record_ref.public_id


def _record_ref_from_model(model: type[models.Model], object_id: Any) -> RecordRef:
    """Return a record ref from an already resolved model and primary key."""

    return RecordRef(
        model_label=model._meta.label,
        object_id=object_id,
        public_id=public_id_for(model, object_id),
        resource_type=model_resource_type(model) or "",
    )


def _empty_record_ref(object_id: Any | None = None) -> RecordRef:
    """Return the empty reference used for unset or stale contenttypes."""

    return RecordRef(model_label="", object_id=object_id, public_id="", resource_type="")
