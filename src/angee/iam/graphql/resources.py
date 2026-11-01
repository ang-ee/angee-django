"""Native IAM query/backend/metadata contributions to generic GraphQL resources."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from contextlib import nullcontext

from django.db import models
from rebac import PermissionDenied, RebacMixin, current_actor, system_context
from rebac.managers import RebacQuerySet
from rebac.relation_loading import relation_actor
from rebac.resources import model_for_resource_type, model_resource_type

from angee.base.refs import generic_pointer_model
from angee.graphql.data import contracts as data_contract
from angee.graphql.data.extensions import ResourceExtensions
from angee.graphql.data.hasura import AngeeHasuraWriteBackend
from angee.iam.graphql.access import assert_no_gated_read_fields
from angee.iam.graphql.relations import (
    RecordReferenceNode,
    actor_scoped_relation_expression,
    with_record_reference_access,
)
from angee.iam.graphql.writes import write_queryset
from angee.iam.models.base import ProtectedModel
from angee.iam.models.refs import ProtectedRecordRefMixin, record_edges_by_target
from angee.iam.permissions import declared_rebac_definition, require_rebac_queryset, require_record_access


class ProtectedHasuraWriteBackend(AngeeHasuraWriteBackend):
    def _save_locked_parent(self, info, instance, patch, line_rows, *, expected_revision):
        require_record_access(instance, "write")
        return super()._save_locked_parent(info, instance, patch, line_rows, expected_revision=expected_revision)


def _aggregate(rows):
    return rows.scoped_for_aggregate() if isinstance(rows, RebacQuerySet) else rows


def _relation_queryset(model, source):
    return require_rebac_queryset(model).with_actor(relation_actor(source)).with_action("read")


def _validate_fields(model, fields, owner, reason):
    assert_no_gated_read_fields(model, fields, owner, reason, definition_for=declared_rebac_definition)


def _prepare_queryset(rows, node, required):
    if issubclass(node, RecordReferenceNode) or required:
        return with_record_reference_access(rows, select=False)
    return rows


def _validate_delete(instance):
    if not instance.has_access("delete"):
        raise PermissionDenied("You are not allowed to delete this record.")


def _preview_queryset(model, actor):
    actor = actor if actor is not None else current_actor()
    rows = require_rebac_queryset(model).with_actor(actor).with_action("read")
    return rows if actor is not None else rows.none()


def _declaration_queryset(model):
    if issubclass(model, ProtectedModel):
        return model.system_queryset()
    return require_rebac_queryset(model).system_context(reason="iam.graphql.declaration")


def contribute(model):
    common = ResourceExtensions(
        relation_expression=actor_scoped_relation_expression,
        validate_fields=_validate_fields,
        prepare_queryset=_prepare_queryset,
    )
    if not issubclass(model, RebacMixin):
        return common
    return dataclasses.replace(
        common,
        aggregate_queryset=_aggregate,
        write_queryset=write_queryset,
        declaration_queryset=_declaration_queryset,
        relation_queryset=_relation_queryset,
        write_backend_factory=ProtectedHasuraWriteBackend,
        delete_context=lambda reason: system_context(reason=reason) if reason is not None else nullcontext(),
        validate_delete=_validate_delete,
        preview_queryset=_preview_queryset,
    )


def enrich_metadata(resources):
    by_model = {item.model: item for item in resources if item.model is not None}
    edges = record_edges_by_target(definition_for=declared_rebac_definition)
    return tuple(
        dataclasses.replace(
            item,
            resource_type=model_resource_type(item.model) if item.model is not None else None,
            grantable=_grantable_relations(item.model, by_model),
            record_edges=_record_edges(item.model, edges),
        )
        for item in resources
    )


def _record_edges(
    model: type[models.Model] | None,
    edges_by_target: Mapping[type[models.Model], tuple[type[ProtectedRecordRefMixin], ...]],
) -> tuple[str, ...]:
    """Project the edge models accepting this resource's canonical type as a target.

    A record carries only these edges, so a surface listing one (a record's files,
    pages or source conversations) applies to the record types that name it. An
    edge with roles adds ``<label>:<role>`` for each role the type carries
    (:meth:`~angee.iam.models.refs.ProtectedRecordRefMixin.record_edge_roles`), so a surface
    serving one role (Comments on the chatter role) gates on that entry.
    """

    if model is None:
        return ()
    entries: list[str] = []
    for edge in edges_by_target.get(generic_pointer_model(model), ()):
        entries.append(edge._meta.label)
        entries.extend(f"{edge._meta.label}:{role}" for role in edge.record_edge_roles(model))
    return tuple(entries)


def _grantable_relations(
    model: type[models.Model] | None,
    resources_by_model: dict[type[models.Model], data_contract.DataResourceMetadata],
) -> tuple[data_contract.GrantableRelationMetadata, ...]:
    """Project grants through resources exposing their model's subject relation."""

    if model is None or not issubclass(model, ProtectedModel):
        return ()
    declaration = model.get_rebac_grantable()
    definition = declared_rebac_definition(model)
    if not declaration or definition is None:
        return ()
    relations = {relation.name: relation for relation in definition.relations}
    result: list[data_contract.GrantableRelationMetadata] = []
    for relation_name, permission in sorted(declaration.items()):
        relation = relations.get(relation_name)
        if relation is None:
            continue
        subjects: list[data_contract.RecordAccessSubjectMetadata] = []
        for allowed in relation.allowed_subjects:
            if allowed.wildcard or allowed.with_caveat or allowed.id:
                continue
            subject_model = model_for_resource_type(allowed.type)
            subject_resource = resources_by_model.get(subject_model) if subject_model is not None else None
            subjects.append(
                data_contract.RecordAccessSubjectMetadata(
                    type=allowed.type,
                    relation=allowed.relation or None,
                    resource=(
                        subject_resource.model_label
                        if subject_resource is not None
                        and subject_model is not None
                        and subject_resource.subject_field is not None
                        and (getattr(subject_model._meta, "rebac_subject_relation", "") or "")
                        == (allowed.relation or "")
                        else None
                    ),
                )
            )
        result.append(
            data_contract.GrantableRelationMetadata(
                relation=relation_name,
                permission=permission,
                subjects=tuple(subjects),
            )
        )
    return tuple(result)
