"""Generic record references backed by Django contenttypes.

A polymorphic edge and a REBAC grant see a multi-table-inheritance row from
different sides. An edge stores :func:`rebac.generic_target`, which owns the
canonical write identity: a row is named by its topmost REBAC-typed MTI
ancestor, and a row without a REBAC type cannot be named at all. The two edges
that also name rows outside REBAC — a chatter thread on an ungated host, an
import record link to a plain sink — store :func:`generic_pointer_target`, which falls
back to Django's own generic-pointer identity for such a row.
:func:`ancestor_object_refs` owns the read/grant fan-out.
Model-owned edge contracts live in :mod:`angee.base.models.refs`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Self

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac import ObjectRef, to_object_ref
from rebac.field_backing import canonical_model
from rebac.resources import model_resource_type

from angee.base.lineage import concrete_child_models
from angee.base.models.identity import public_id_for


@dataclass(frozen=True, slots=True)
class RecordRef:
    """Frozen public identity for a model row reached through a generic pointer."""

    model_label: str
    object_id: Any | None
    public_id: str
    resource_type: str

    @classmethod
    def from_instance(cls, instance: models.Model) -> Self:
        """Project an instance through this value's canonical model/primary-key constructor."""

        return cls.from_model(type(instance), instance.pk)

    @classmethod
    def from_model(cls, model: type[models.Model], object_id: Any) -> Self:
        """Build a public reference from an already resolved model and primary key."""

        return cls(
            model_label=model._meta.label,
            object_id=object_id,
            public_id=public_id_for(model, object_id),
            resource_type=model_resource_type(model) or "",
        )


def record_view_refs(*records: RecordRef, using: str | None = None) -> tuple[RecordRef, ...]:
    """Include existing inherited views of canonical records, querying ids in batches."""

    result = list(dict.fromkeys(records))
    grouped: dict[type[models.Model], set[Any]] = {}

    def children(parent: type[models.Model], ids: set[Any]) -> None:
        for child in concrete_child_models(parent):
            grouped.setdefault(child, set()).update(ids)
            children(child, ids)

    for record in result:
        children(apps.get_model(record.model_label), {record.object_id})
    for child, ids in grouped.items():
        present = child._base_manager.using(using).filter(pk__in=ids).values_list("pk", flat=True)
        result.extend(RecordRef.from_model(child, pk) for pk in present)
    return tuple(dict.fromkeys(result))


def ancestor_object_refs(obj: models.Model) -> tuple[ObjectRef, ...]:
    """Return every REBAC identity ``obj`` IS-A, nearest identity first.

    The **read/grant fan-out** dual of :func:`rebac.generic_target`: ``obj``'s own
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


def generic_pointer_model(model: type[models.Model]) -> type[models.Model]:
    """Return the model a generic pointer stores for rows of ``model``.

    A gated row is named by its canonical REBAC model
    (:func:`rebac.field_backing.canonical_model`), so a multi-table child shares
    its typed parent's edges and the schema's target relations reach it. An
    ungated row, which no schema relation can name, keeps its own concrete
    model: Django's generic-pointer default. Only an edge that admits ungated
    rows (a chatter thread, an import record link) uses this; an edge whose
    schema authorizes its target stores :func:`rebac.generic_target` directly.
    """

    return canonical_model(model) or model._meta.concrete_model or model


def generic_pointer_target(record: models.Model) -> tuple[ContentType, Any]:
    """Return the content type and id a generic pointer stores for ``record``.

    :func:`rebac.generic_target`'s identity for a gated row, Django's own for an
    ungated one; see :func:`generic_pointer_model`.
    """

    content_type = ContentType.objects.db_manager(record._state.db, hints={"instance": record}).get_for_model(
        generic_pointer_model(type(record))
    )
    return content_type, record.pk


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


def canonical_link_path(model: type[models.Model]) -> tuple[str, ...]:
    """Return the primary-key parent links from ``model`` up to its canonical REBAC model.

    A relation declared on the canonical model, such as the ``GenericRelation`` a
    polymorphic edge keys on through :func:`rebac.generic_target`, is reached from a
    multi-table child through these links: Django's generic relation would key the
    child's rows on the child's own content type, which no edge stores. Empty for a
    canonical model, a proxy of one, or an ungated model.
    """

    target = canonical_model(model)
    if target is None:
        return ()
    chain = list(_pk_ancestor_chain(model._meta.concrete_model or model))
    if target not in chain:
        raise ValueError(f"{model._meta.label} does not share its primary key with {target._meta.label}.")
    links = []
    for step, parent in zip(chain, chain[1:], strict=False):
        if step is target:
            break
        link = step._meta.parents[parent]
        if link is None:
            raise ImproperlyConfigured(f"{step._meta.label} has no concrete parent link to {parent._meta.label}.")
        links.append(link.name)
    return tuple(links)
