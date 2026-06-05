"""Library-backed CRUD mutation surfaces for Strawberry schemas."""

from __future__ import annotations

from typing import Any

import strawberry
import strawberry_django
from strawberry.annotation import StrawberryAnnotation
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from rebac import system_context
from strawberry import UNSET, relay
from strawberry.extensions.field_extension import FieldExtension, SyncExtensionResolver
from strawberry.types import Info
from strawberry_django.mutations import resolvers as mutation_resolvers
from strawberry_django.mutations.fields import (
    DjangoUpdateMutation,
    get_pk,
    get_vdata,
)
from strawberry_django.permissions import filter_with_perms

from angee.base.models import instance_from_public_id
from angee.graphql.deletion import DeletePreview
from angee.graphql.introspection import django_model, surface_name


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
    return strawberry.type(surface)


class _AngeeUpdateMutation(DjangoUpdateMutation):
    """Update mutation whose write target is loaded without field redaction."""

    def instance_level_update(
        self,
        info: Info,
        kwargs: dict[str, Any],
        data: Any,
    ) -> Any:
        model = self.django_model
        assert model is not None

        vdata = get_vdata(data)
        pk = get_pk(vdata, key_attr=self.key_attr)

        if pk not in (None, UNSET):  # noqa: PLR6201
            instance = _resolve_for_write(model, pk, key_attr=self.key_attr)
        else:
            instance = filter_with_perms(
                self.get_queryset(
                    queryset=_write_queryset(model),
                    info=info,
                    **kwargs,
                ),
                info,
            )

        return self.update(
            info,
            instance,
            mutation_resolvers.parse_input(info, vdata, key_attr=self.key_attr),
        )


def _update_mutation(
    input_type: type,
    *,
    permission_classes: list[type] | None,
    extensions: list[FieldExtension] | None,
) -> _AngeeUpdateMutation:
    """Return Angee's Strawberry-Django update field."""

    return _AngeeUpdateMutation(
        input_type,
        python_name=None,
        django_name=None,
        graphql_name=None,
        type_annotation=StrawberryAnnotation.from_annotation(None),
        permission_classes=permission_classes or [],
        extensions=extensions or (),
    )


def _write_queryset(model: type[models.Model]) -> models.QuerySet[models.Model]:
    """Return a write-target queryset that preserves row scope, not redaction."""

    queryset = model._default_manager.all()
    on_field_deny = getattr(queryset, "on_field_deny", None)
    if callable(on_field_deny):
        queryset = on_field_deny("allow")
    return queryset


def _resolve_for_write(
    model: type[models.Model],
    key: Any,
    *,
    key_attr: str | None,
) -> models.Model:
    """Return a write-ready instance addressed by mutation input."""

    queryset = _write_queryset(model)
    if isinstance(key, relay.GlobalID):
        instance = instance_from_public_id(model, key.node_id, queryset=queryset)
    elif key_attr in (None, "id"):
        instance = instance_from_public_id(model, str(key), queryset=queryset)
    else:
        try:
            instance = queryset.filter(**{key_attr: key}).first()
        except (TypeError, ValueError):
            instance = None
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {key!r} was not found")
    return instance


def _delete_resolver(model: type[models.Model]) -> Any:
    """Return a mutation resolver that previews then deletes."""

    def delete(id: relay.GlobalID, confirm: bool = False) -> DeletePreview:
        """Delete one model instance by global id when unblocked."""

        with transaction.atomic():
            instance = _resolve_for_delete(model, id.node_id)
            preview = DeletePreview.from_instance(instance)
            if confirm and not preview.has_blockers:
                instance.delete()
        return preview

    return delete


def _resolve_for_delete(
    model: type[models.Model],
    public_id: str,
) -> models.Model:
    """Return the instance addressed by ``public_id`` or raise."""

    instance = instance_from_public_id(model, public_id, queryset=_write_queryset(model))
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {public_id!r} was not found")
    return instance
