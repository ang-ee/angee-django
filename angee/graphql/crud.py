"""Library-backed CRUD mutation surfaces for Strawberry schemas."""

from __future__ import annotations

from typing import Any

import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac import system_context
from strawberry import UNSET
from strawberry.annotation import StrawberryAnnotation
from strawberry.extensions.field_extension import FieldExtension, SyncExtensionResolver
from strawberry.types import Info
from strawberry_django.mutations import resolvers as mutation_resolvers
from strawberry_django.mutations.fields import DjangoUpdateMutation, get_pk, get_vdata
from strawberry_django.permissions import filter_with_perms

from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.metadata import (
    DataResourceRoots,
    DataResourceTypeNames,
    attach_data_resource_metadata,
    make_data_resource_metadata,
    resource_type_name,
    resource_wire_field_name,
)
from angee.graphql.deletion import DeletePreview, delete_by_public_id
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.introspection import django_model, surface_name
from angee.graphql.writes import write_queryset


class _SystemContextWrite(FieldExtension):
    """Run an elevated CRUD write under ``system_context``, after the field's gate.

    ``crud(..., write_context=…)`` attaches this to create/update/delete for an
    admin console surface whose REBAC per-row ``create`` gate can't apply to a
    not-yet-inserted row (the sqid only exists post-insert). The ``permission_classes``
    on the field are the authorization (checked first, with the request actor); this
    extension then runs the write elevated so the unsatisfiable per-row gate is bypassed —
    the same shape the IAM managers use for const-admin writes.
    """

    def __init__(self, reason: str) -> None:
        """Store the ``system_context`` reason recorded for the elevated write."""

        self._reason = reason

    def resolve(self, next_: SyncExtensionResolver, source: Any, info: Info, **kwargs: Any) -> Any:
        """Resolve the wrapped write under ``system_context``."""

        with system_context(reason=self._reason):
            return next_(source, info, **kwargs)


def crud(
    node: type,
    *,
    create: type | None = None,
    update: type | None = None,
    delete: bool = False,
    name: str | None = None,
    permission_classes: list[type] | None = None,
    write_context: str | None = None,
) -> type:
    """Return a Strawberry mutation surface for one Django model type.

    ``write_context`` runs the create/update/delete writes under ``system_context``
    (with that reason), gated by ``permission_classes`` — for admin console surfaces
    whose const-backed per-row REBAC ``create`` cannot apply to a not-yet-inserted row.
    """

    model = django_model(node)
    singular = name or model._meta.model_name
    annotations: dict[str, Any] = {}
    namespace: dict[str, Any] = {"__annotations__": annotations}

    def add(verb: str, annotation: Any, field: Any) -> None:
        """Add one operation field to the generated surface."""

        attr = f"{verb}_{singular}"
        annotations[attr] = annotation
        namespace[attr] = field

    def write_extensions() -> list[FieldExtension] | None:
        """Return a fresh elevated-write extension list when a write context is set."""

        return [_SystemContextWrite(write_context)] if write_context else None

    if create is not None:
        add(
            "create",
            node,
            strawberry_django.mutations.create(
                create,
                permission_classes=permission_classes,
                extensions=write_extensions(),
            ),
        )
    if update is not None:
        add(
            "update",
            node,
            _update_mutation(
                update,
                permission_classes=permission_classes,
                extensions=write_extensions(),
            ),
        )
    if delete:
        add(
            "delete",
            DeletePreview,
            strawberry.mutation(
                resolver=_delete_resolver(model),
                permission_classes=permission_classes,
                extensions=write_extensions() or [],
            ),
        )

    if not annotations:
        raise ImproperlyConfigured(f"crud({surface_name(node)}) needs at least one of create, update, or delete")
    type_name = f"{singular[:1].upper()}{singular[1:]}Mutation"
    surface = type(type_name, (), namespace)
    surface = strawberry.type(surface)
    return attach_data_resource_metadata(
        surface,
        make_data_resource_metadata(
            model=model,
            node_type=node,
            roots=DataResourceRoots(
                create_name=resource_wire_field_name(surface, f"create_{singular}") if create is not None else None,
                update_name=resource_wire_field_name(surface, f"update_{singular}") if update is not None else None,
                delete_preview_name=resource_wire_field_name(surface, f"delete_{singular}") if delete else None,
            ),
            type_names=DataResourceTypeNames(
                node=resource_type_name(node),
                create_input=resource_type_name(create),
                update_input=resource_type_name(update),
                delete_payload=resource_type_name(DeletePreview) if delete else None,
            ),
            create_input_type=create,
            update_input_type=update,
            capabilities=tuple(
                name
                for name, enabled in (
                    ("create", create is not None),
                    ("update", update is not None),
                    ("deletePreview", delete),
                )
                if enabled
            ),
        ),
    )


def _write_queryset(model: type[models.Model]) -> models.QuerySet[models.Model]:
    """Return a write-target queryset: REBAC row scope kept, field-read redaction off.

    Both the update apply and the delete-to-history step read the in-memory
    instance, so the write target must load every column (redaction off) while
    staying row-scoped. REBAC models expose this as ``for_write()``; plain Django
    models have no field redaction and use their default manager.
    """

    return write_queryset(model)


class _AngeeUpdateMutation(DjangoUpdateMutation):
    """Update mutation that loads the write target through ``_write_queryset``.

    The stock ``instance_level_update`` resolves the public-id-addressed write
    target through ``get_with_perms``/``_default_manager.get`` (and the bulk
    branch through ``get_queryset``), neither of which applies the REBAC
    redaction-off write scope. Resolving an update target must read every column
    to mutate it, so field-read redaction must stay off (a redacted column would
    overwrite the stored value on save) while row scope stays on. This override
    is the single seam that pins both branches to ``_write_queryset``; input
    parsing, the ``update`` apply, and m2m handling stay the stock library's.
    """

    def instance_level_update(self, info: Info, kwargs: dict[str, Any], data: Any) -> Any:
        model = self.django_model
        assert model is not None

        vdata = get_vdata(data)
        pk = get_pk(vdata, key_attr=self.key_attr)
        write_target = _write_queryset(model)

        if pk not in (None, UNSET):  # noqa: PLR6201
            instance: Any = require_instance_for_id(model, pk, queryset=write_target)
        else:
            instance = filter_with_perms(self.get_queryset(queryset=write_target, info=info, **kwargs), info)

        return self.update(info, instance, mutation_resolvers.parse_input(info, vdata, key_attr=self.key_attr))


def _delete_resolver(model: type[models.Model]) -> Any:
    """Return a mutation resolver that previews then deletes by public id."""

    def delete(id: PublicID, confirm: bool = False) -> DeletePreview:
        """Delete one model instance by public id when unblocked."""

        return delete_by_public_id(model, str(id), confirm=confirm, queryset=_write_queryset(model))

    return delete


def _update_mutation(
    input_type: type,
    *,
    permission_classes: list[type] | None,
    extensions: list[FieldExtension] | None,
) -> _AngeeUpdateMutation:
    """Return Angee's Strawberry-Django update field keyed by the public id."""

    return _AngeeUpdateMutation(
        input_type,
        python_name=None,
        django_name=None,
        graphql_name=None,
        type_annotation=StrawberryAnnotation.from_annotation(None),
        key_attr=PUBLIC_ID_FIELD_NAME,
        permission_classes=permission_classes or [],
        extensions=extensions or (),
    )
