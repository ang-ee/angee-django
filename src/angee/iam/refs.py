"""Native REBAC identities and stored relationship retention."""

from __future__ import annotations

from django.db import models
from rebac import ObjectRef, model_identities, to_object_ref
from rebac.actors import model_can_resolve_subject
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type

from angee.base.lineage import concrete_lineage
from angee.base.refs import _pk_ancestor_chain


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


def named_in_relationships(record: models.Model) -> bool:
    """Return whether stored REBAC relationships name any row retiring ``record`` deletes.

    The delete takes the record's multi-table parents and children with it, and
    REBAC cleans every identity those rows carry, as a resource or as a subject.
    Relations backed by fields follow a moved key; stored rows (a direct share,
    a membership) would go with the retired record.
    """

    model = type(record)
    rows = active_relationship_model().objects.db_manager(hints={"instance": record})
    identities = set()
    for owner in concrete_lineage(model):
        if not model_resource_type(owner) and not model_can_resolve_subject(owner):
            continue
        row = owner._base_manager.db_manager(hints={"instance": record}).filter(pk=record.pk).first()
        if row is not None:
            identities.update(model_identities(owner, row))
    return rows.referencing(identities).exists()
