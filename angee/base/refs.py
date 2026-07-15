"""Generic record references backed by Django contenttypes.

Also the owner of the **record-target-across-MTI policy**: a polymorphic edge and a
REBAC grant see a multi-table-inheritance row from different sides.
:func:`canonical_record_model` and :func:`canonical_record_target` own the write
identity; :func:`ancestor_object_refs` owns the read/grant fan-out.

**Placement invariant.** A polymorphic edge that keys on
:func:`canonical_record_target` — ``storage.FileAttachment``, ``tags.TagAssignment``,
``messaging.ThreadAttachment``, and every reverse ``GenericRelation`` onto such an edge
(``messaging.ThreadedModelMixin.thread_attachments``, a future ``tags`` relation on
``Party``) — must be declared on, and any mixin owning it composed onto, the *same*
topmost REBAC-typed MTI ancestor the canonical write keys on. A reverse
``GenericRelation`` filters at its declaring model's own content type, so composing the
mixin on a child while its canonical ancestor does not splits the write content type
from the collect content type and orphans edge rows on delete.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar, NamedTuple

from django.contrib.contenttypes.models import ContentType
from django.db import models
from rebac import ObjectRef, to_object_ref
from rebac.resources import model_resource_type

from angee.base.models import public_id_for


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
    """Project a contenttypes-backed row reference from model-owned fields."""

    record_ref_field_prefix: ClassVar[str] = "target"
    """Reference field prefix; ``target`` maps to ``content_type``/``object_id``."""

    class Meta:
        """Django model options for record-ref-only abstract inheritance."""

        abstract = True

    @property
    def record_ref(self) -> RecordRef:
        """Return this row's referenced record identity without loading the target."""

        content_type_id = getattr(self, self._record_ref_content_type_id_attr(), None)
        object_id = getattr(self, self._record_ref_object_id_field_name(), None)
        if content_type_id in (None, "") or object_id in (None, ""):
            return _empty_record_ref(object_id)
        model = ContentType.objects.get_for_id(content_type_id).model_class()
        if model is None:
            return _empty_record_ref(object_id)
        return _record_ref_from_model(model, object_id)

    @property
    def record_model_label(self) -> str:
        """Return the referenced record's ``app_label.ModelName`` label."""

        return self.record_ref.model_label

    @property
    def record_public_id(self) -> str:
        """Return the referenced record's stable public id."""

        return self.record_ref.public_id

    @classmethod
    def _record_ref_content_type_field_name(cls) -> str:
        """Return the content-type FK field that backs this reference."""

        prefix = cls.record_ref_field_prefix
        if prefix == "target":
            return "content_type"
        return f"{prefix}_content_type"

    @classmethod
    def _record_ref_content_type_id_attr(cls) -> str:
        """Return the stored content-type id attribute name."""

        return f"{cls._record_ref_content_type_field_name()}_id"

    @classmethod
    def _record_ref_object_id_field_name(cls) -> str:
        """Return the object-id field that backs this reference."""

        prefix = cls.record_ref_field_prefix
        if prefix == "target":
            return "object_id"
        return f"{prefix}_object_id"


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
