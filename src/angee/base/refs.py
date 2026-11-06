"""Generic record identities and pointers owned by native Django contenttypes."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Self

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from angee.base.lineage import concrete_child_models
from angee.base.models.identity import PublicIdentity, public_id_for
from angee.base.querysets import by_key


@dataclass(frozen=True, slots=True)
class RecordRef:
    """Frozen public identity for a model row reached through a generic pointer."""

    model_label: str
    object_id: Any | None
    public_id: str

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
        rows = child._base_manager.using(using).order_by("pk")
        for batch in by_key(rows, sorted(pk for pk in ids if pk is not None)):
            result.extend(RecordRef.from_model(child, pk) for pk in batch.values_list("pk", flat=True))
    return tuple(dict.fromkeys(result))



def generic_pointer_model(model: type[models.Model]) -> type[models.Model]:
    """Ask a public model's identity owner, otherwise use Django's concrete model."""
    return model.generic_pointer_model() if issubclass(model, PublicIdentity) else model._meta.concrete_model or model


def generic_pointer_target(record: models.Model) -> tuple[ContentType, Any]:
    """Return the content type and id a generic pointer stores for ``record``.

    Protected rows use their REBAC canonical model; other rows use Django's
    concrete model. See :func:`generic_pointer_model`.
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
    """Return the primary-key parent links from ``model`` up to its canonical record model.

    A relation declared on the canonical model, such as the ``GenericRelation`` a
    polymorphic edge keys on through the model identity owner, is reached from a
    multi-table child through these links: Django's generic relation would key the
    child's rows on the child's own content type, which no edge stores. Empty for a
    canonical model, a proxy of one, or an ungated model.
    """

    target = generic_pointer_model(model)
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
