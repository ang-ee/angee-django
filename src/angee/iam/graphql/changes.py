"""Native IAM event identity and per-subscriber filtering contributions."""

from __future__ import annotations

from dataclasses import replace

from django.db import models
from rebac import ObjectRef, current_actor
from rebac.resources import model_resource_id, model_resource_type

from angee.graphql.events import ChangePayload
from angee.iam.graphql.access import ChangeReadGate


def project(instance: models.Model, change: ChangePayload) -> ChangePayload:
    resource_id = model_resource_id(instance) if model_resource_type(type(instance)) else None
    anchor = _change_read_resource(instance) if change.broadcasts else None
    return replace(
        change,
        resource_id=resource_id,
        read_resource_type=None if anchor is None else anchor.resource_type,
        read_resource_id=None if anchor is None else anchor.resource_id,
    )


def filter_event(model, info, gate_type):
    actor = current_actor()
    if actor is None:
        return None
    gate = (gate_type or ChangeReadGate)(model, actor)
    return gate.filter


def _change_read_resource(instance: models.Model) -> ObjectRef | None:
    """Return an instance-owned change-feed read anchor, if declared.

    Most rows are gated by their own REBAC identity. A child or polymorphic edge
    whose visibility derives from another row may expose ``change_read_resource``;
    capturing that :class:`~rebac.ObjectRef` while the instance is still live keeps
    create, update, and delete events on the same authorization boundary.
    """

    resolver = getattr(instance, "change_read_resource", None)
    if not callable(resolver):
        return None
    resource = resolver()
    if resource is not None and not isinstance(resource, ObjectRef):
        raise TypeError("change_read_resource() must return rebac.ObjectRef or None")
    return resource
