"""Library-backed CRUD mutation surfaces for Strawberry schemas."""

from __future__ import annotations

from typing import Any

import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from strawberry import relay

from angee.base.deletion import (
    DeletionPreview,
    DeletionPreviewGroup,
    DeletionPreviewNode,
)
from angee.base.graphql.introspection import django_model, surface_name
from angee.base.models import instance_from_public_id


@strawberry.type
class DeletePreviewGroup:
    """GraphQL output for one deletion preview group."""

    label: str
    count: int

    @classmethod
    def from_domain(cls, group: DeletionPreviewGroup) -> DeletePreviewGroup:
        """Return GraphQL output for a domain preview group."""

        return cls(label=group.label, count=group.count)


@strawberry.type
class DeletePreviewNode:
    """GraphQL output for one deletion preview tree node."""

    label: str
    object_label: str
    object_id: str | None
    children: list["DeletePreviewNode"]

    @classmethod
    def from_domain(cls, node: DeletionPreviewNode) -> DeletePreviewNode:
        """Return GraphQL output for a domain preview tree node."""

        return cls(
            label=node.label,
            object_label=node.object_label,
            object_id=node.object_id,
            children=[DeletePreviewNode.from_domain(child) for child in node.children],
        )


@strawberry.type
class DeletePreview:
    """GraphQL output for a cascade deletion preview."""

    total_deleted_count: int
    deleted: list[DeletePreviewGroup]
    updated: list[DeletePreviewGroup]
    blocked: list[DeletePreviewGroup]
    has_blockers: bool
    root: DeletePreviewNode = strawberry.field(
        description="Tree apex for the target row; deleted counts already include that row."
    )

    @classmethod
    def from_domain(cls, preview: DeletionPreview) -> DeletePreview:
        """Return GraphQL output for a domain deletion preview."""

        return cls(
            total_deleted_count=preview.total_deleted_count,
            deleted=[DeletePreviewGroup.from_domain(group) for group in preview.deleted],
            updated=[DeletePreviewGroup.from_domain(group) for group in preview.updated],
            blocked=[DeletePreviewGroup.from_domain(group) for group in preview.blocked],
            has_blockers=preview.has_blockers,
            root=DeletePreviewNode.from_domain(preview.root),
        )


def crud(
    node: type,
    *,
    create: type | None = None,
    update: type | None = None,
    delete: bool = False,
    name: str | None = None,
    permission_classes: list[type] | None = None,
) -> type:
    """Return a Strawberry mutation surface for one Django model type."""

    model = django_model(node)
    singular = name or model._meta.model_name
    annotations: dict[str, Any] = {}
    namespace: dict[str, Any] = {"__annotations__": annotations}

    def add(verb: str, annotation: Any, field: Any) -> None:
        """Add one operation field to the generated surface."""

        attr = f"{verb}_{singular}"
        annotations[attr] = annotation
        namespace[attr] = field

    if create is not None:
        add(
            "create",
            node,
            strawberry_django.mutations.create(
                create,
                permission_classes=permission_classes,
            ),
        )
    if update is not None:
        add(
            "update",
            node,
            strawberry_django.mutations.update(
                update,
                permission_classes=permission_classes,
            ),
        )
    if delete:
        add(
            "delete",
            DeletePreview,
            strawberry.mutation(
                resolver=_delete_resolver(model),
                permission_classes=permission_classes,
            ),
        )

    if not annotations:
        raise ImproperlyConfigured(f"crud({surface_name(node)}) needs at least one of create, update, or delete")
    type_name = f"{singular[:1].upper()}{singular[1:]}Mutation"
    surface = type(type_name, (), namespace)
    return strawberry.type(surface)


def _delete_resolver(model: type[models.Model]) -> Any:
    """Return a mutation resolver that previews then deletes."""

    def delete(id: relay.GlobalID, confirm: bool = True) -> DeletePreview:
        """Delete one model instance by global id when unblocked."""

        with transaction.atomic():
            instance = _resolve_for_delete(model, id.node_id)
            preview = DeletionPreview.from_instance(instance)
            if confirm and not preview.has_blockers:
                instance.delete()
        return DeletePreview.from_domain(preview)

    return delete


def _resolve_for_delete(
    model: type[models.Model],
    public_id: str,
) -> models.Model:
    """Return the instance addressed by ``public_id`` or raise."""

    instance = instance_from_public_id(model, public_id)
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {public_id!r} was not found")
    return instance
