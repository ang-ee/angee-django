"""Checked installation of native Workflow, Step and Edge resource row groups.

The resource loader keeps its ordinary ordering, transaction and ledger. These
models opt into one model-owned import command so graph rows are never imported
directly around the definition manager's validation and revision counter.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ValidationError

from angee.resources.entries import LoadResult
from angee.resources.exceptions import ResourceLoadError
from angee.resources.widgets import split_xref
from angee.workflows.attempts import json_values_equal
from angee.workflows.definitions import (
    DefinitionEdit,
    DefinitionEditError,
    DefinitionReadinessError,
    EdgeCreate,
    EdgeDelete,
    EdgePatch,
    EndpointRef,
    NodeCreate,
    NodeDelete,
    NodePatch,
)


def _rows(group: Any) -> list[dict[str, Any]]:
    headers = tuple(group.dataset.headers or ())
    return [dict(zip(headers, row, strict=True)) for row in group.dataset]


def _declaration_values(resource: Any, row: Mapping[str, Any], names: frozenset[str]) -> dict[str, Any]:
    """Coerce declared values and apply the Django model's omission policy."""

    values: dict[str, Any] = {}
    for name in sorted(names):
        model_field = resource._meta.model._meta.get_field(name)
        if name in row and row[name] is not None:
            values[name] = resource.fields[name].clean(row)
        elif model_field.has_default():
            values[name] = copy.deepcopy(model_field.get_default())
        elif model_field.null:
            values[name] = None
        else:
            raise ResourceLoadError(f"{resource.entry.display}: missing required declaration field {name!r}")
    return values


def _import_export_patch(resource: Any, instance: Any, wanted: Mapping[str, Any]) -> dict[str, Any]:
    """Return declared changes through each import-export field widget."""

    patch: dict[str, Any] = {}
    for name, value in wanted.items():
        field = resource.fields[name]
        if field.widget.render(field.get_value(instance)) != field.widget.render(value):
            patch[name] = value
    return patch


def _handle(value: str, aliases: Mapping[str, str]) -> tuple[str, str]:
    try:
        return split_xref(value, aliases)
    except ValueError as error:
        raise ResourceLoadError(f"Invalid workflow declaration reference {value!r}") from error


def import_resource_groups(
    workflow_model: type[Any],
    groups: tuple[tuple[Any, Any], ...],
    *,
    ledger_model: type[Any],
    addon_aliases: Mapping[str, str],
) -> LoadResult:
    """Compare declarations, retain omitted same-impl config, then publish."""

    step_model = workflow_model._meta.get_field("steps").related_model
    edge_model = workflow_model._meta.get_field("edges").related_model
    by_model: dict[type[Any], list[tuple[Any, Any, dict[str, Any]]]] = defaultdict(list)
    for group, resource in groups:
        if group.model not in {workflow_model, step_model, edge_model}:
            raise ResourceLoadError(f"{group.entry.display}: unrelated group under workflow import owner")
        resource.before_import(group.dataset)
        for row in _rows(group):
            by_model[group.model].append((group, resource, row))
    heads: dict[tuple[str, str], Any] = {}
    workflow_resources: dict[int, Any] = {}
    publications: dict[int, bool] = {}
    counts = {"created": 0, "updated": 0, "skipped": 0}
    try:
        # Make every head addressable before resolving cross-workflow references.
        for group, resource, row in by_model[workflow_model]:
            xref = str(row["_xref"])
            key = (group.entry.addon.name, xref)
            if key in heads:
                raise ResourceLoadError(f"{group.entry.display}: duplicated workflow xref {xref!r}")
            instance = resource.declared_instance(row)
            declared_key = row.get("key")
            if instance is None:
                name = row.get("name")
                if not isinstance(name, str) or not name:
                    raise ResourceLoadError(f"{group.entry.display}: workflow name is required")
                instance = workflow_model(name=name, key=declared_key or "")
                instance.save()
            elif instance.published_from_id is not None:
                raise ResourceLoadError(f"{group.entry.display}: a resource declaration must target a draft head")
            if declared_key not in (None, "", instance.key):
                if instance.key:
                    raise ResourceLoadError(f"{group.entry.display}: stable workflow key cannot change")
                instance.key = declared_key
                instance.save(update_fields=["key", "updated_at"])
            heads[key] = instance
            workflow_resources.setdefault(instance.pk, resource)
            publications[instance.pk] = publications.get(instance.pk, False) or group.entry.publish
            counts[resource.record_declared_instance(row, instance)] += 1

        workflow_fields: dict[int, dict[str, Any]] = {}
        for group, resource, row in by_model[workflow_model]:
            head = heads[(group.entry.addon.name, str(row["_xref"]))]
            wanted = _declaration_values(resource, row, workflow_model.objects._WORKFLOW_FIELDS)
            previous = workflow_fields.setdefault(head.pk, wanted)
            if not json_values_equal(previous, wanted):
                raise ResourceLoadError(f"{group.entry.display}: conflicting workflow declarations")

        declared_nodes: dict[int, list[tuple[Any, Any, dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        for group, resource, row in by_model[step_model]:
            handle = row.get("workflow")
            if not isinstance(handle, str):
                raise ResourceLoadError(f"{group.entry.display}: step needs a workflow xref")
            head = heads.get(_handle(handle, addon_aliases))
            if head is None:
                raise ResourceLoadError(f"{group.entry.display}: step parent is outside this graph declaration")
            if not isinstance(head, workflow_model) or head.published_from_id is not None:
                raise ResourceLoadError(f"{group.entry.display}: step parent must be a draft head")
            wanted = _declaration_values(resource, row, workflow_model.objects._NODE_FIELDS)
            declared_nodes[head.pk].append((group, resource, row, wanted))

        declared_edges: dict[int, list[tuple[Any, Any, dict[str, Any]]]] = defaultdict(list)
        for group, resource, row in by_model[edge_model]:
            handle = row.get("workflow")
            if not isinstance(handle, str):
                raise ResourceLoadError(f"{group.entry.display}: edge needs a workflow xref")
            head = heads.get(_handle(handle, addon_aliases))
            if head is None:
                raise ResourceLoadError(f"{group.entry.display}: edge parent is outside this graph declaration")
            if not isinstance(head, workflow_model) or head.published_from_id is not None:
                raise ResourceLoadError(f"{group.entry.display}: edge parent must be a draft head")
            declared_edges[head.pk].append((group, resource, row))

        all_heads = {head.pk: head for head in heads.values()}
        for head_id in sorted(set(all_heads) | set(declared_nodes) | set(declared_edges)):
            head = all_heads.get(head_id) or workflow_model.objects.get(pk=head_id)
            snapshot = workflow_model.objects.definition_snapshot(head)
            saved_nodes = {node.key: node for node in snapshot.nodes}
            saved_edges = {(edge.source.key, edge.target.key, edge.condition): edge for edge in snapshot.edges}
            wanted_nodes: dict[tuple[str, str], EndpointRef] = {}
            wanted_node_keys: set[str] = set()
            retained_node_ids: set[int] = set()
            node_creates: list[NodeCreate] = []
            node_patches: list[NodePatch] = []
            for group, resource, row, wanted in declared_nodes[head_id]:
                key = wanted["key"]
                if key in wanted_node_keys:
                    raise ResourceLoadError(f"{group.entry.display}: duplicate step key {key!r}")
                wanted_node_keys.add(key)
                existing = resource.declared_instance(row)
                if existing is not None and existing.workflow_id != head_id:
                    raise ResourceLoadError(f"{group.entry.display}: step xref changed workflow parent")
                existing = existing or saved_nodes.get(key)
                config_declared = "config" in row and row["config"] is not None
                if existing is not None and not config_declared and existing.step_class == wanted["step_class"]:
                    # Omission leaves an operator-authored typed policy intact while
                    # another declared field changes. Explicit config, including {},
                    # remains authoritative; a changed implementation starts from its
                    # own default instead of carrying incompatible retained config.
                    wanted["config"] = copy.deepcopy(existing.config)
                candidate = step_model(workflow=head, **wanted)
                candidate.validate_impl_configs()
                wanted["config"] = candidate.config
                if existing is None:
                    client_key = f"node:{row['_xref']}"
                    node_creates.append(NodeCreate(client_key, wanted))
                    wanted_nodes[(group.entry.addon.name, str(row["_xref"]))] = EndpointRef(client_key=client_key)
                else:
                    if existing.pk in retained_node_ids:
                        raise ResourceLoadError(f"{group.entry.display}: two xrefs identify one step")
                    patch = _import_export_patch(resource, existing, wanted)
                    if patch:
                        node_patches.append(NodePatch(existing.pk, patch))
                    wanted_nodes[(group.entry.addon.name, str(row["_xref"]))] = EndpointRef(existing_id=existing.pk)
                    retained_node_ids.add(existing.pk)
            node_deletes = [NodeDelete(node.pk) for node in snapshot.nodes if node.pk not in retained_node_ids]

            wanted_edge_signatures: set[tuple[str, str, str]] = set()
            edge_creates: list[EdgeCreate] = []
            edge_patches: list[EdgePatch] = []
            retained_edge_ids: set[int] = set()
            edge_rows: list[tuple[Any, Any, dict[str, Any], tuple[str, str, str]]] = []
            step_keys_by_xref = {
                (group.entry.addon.name, str(row["_xref"])): wanted["key"]
                for group, _resource, row, wanted in declared_nodes[head_id]
            }
            for group, resource, row in declared_edges[head_id]:
                source_handle, target_handle = row.get("source"), row.get("target")
                if not isinstance(source_handle, str) or not isinstance(target_handle, str):
                    raise ResourceLoadError(f"{group.entry.display}: edge needs source and target xrefs")
                source_xref = _handle(source_handle, addon_aliases)
                target_xref = _handle(target_handle, addon_aliases)
                if source_xref not in wanted_nodes or target_xref not in wanted_nodes:
                    raise ResourceLoadError(f"{group.entry.display}: edge endpoints must be declared in this graph")
                condition = row.get("condition") or ""
                signature = (step_keys_by_xref[source_xref], step_keys_by_xref[target_xref], condition)
                if signature in wanted_edge_signatures:
                    raise ResourceLoadError(f"{group.entry.display}: duplicate edge {signature!r}")
                wanted_edge_signatures.add(signature)
                edge_rows.append((group, resource, row, signature))
                existing = resource.declared_instance(row) or saved_edges.get(signature)
                if existing is not None and existing.workflow_id != head_id:
                    raise ResourceLoadError(f"{group.entry.display}: edge xref changed workflow parent")
                source_ref, target_ref = wanted_nodes[source_xref], wanted_nodes[target_xref]
                if existing is None:
                    edge_creates.append(
                        EdgeCreate(f"edge:{row['_xref']}", source_ref, target_ref, {"condition": condition})
                    )
                else:
                    if existing.pk in retained_edge_ids:
                        raise ResourceLoadError(f"{group.entry.display}: two xrefs identify one edge")
                    retained_edge_ids.add(existing.pk)
                    endpoint_changed = existing.source.key != signature[0] or existing.target.key != signature[1]
                    patch = {"condition": condition} if existing.condition != condition else {}
                    if endpoint_changed or patch:
                        edge_patches.append(
                            EdgePatch(
                                existing.pk,
                                patch,
                                source_ref if endpoint_changed else None,
                                target_ref if endpoint_changed else None,
                            )
                        )
            edge_deletes = [EdgeDelete(edge.pk) for edge in snapshot.edges if edge.pk not in retained_edge_ids]
            workflow_patch = _import_export_patch(
                workflow_resources[head_id],
                snapshot.workflow,
                workflow_fields.get(head_id, {}),
            )
            edit = DefinitionEdit(
                workflow=workflow_patch,
                node_creates=tuple(node_creates),
                node_patches=tuple(node_patches),
                node_deletes=tuple(node_deletes),
                edge_creates=tuple(edge_creates),
                edge_patches=tuple(edge_patches),
                edge_deletes=tuple(edge_deletes),
            )
            changed = bool(
                workflow_patch
                or node_creates
                or node_patches
                or node_deletes
                or edge_creates
                or edge_patches
                or edge_deletes
            )
            revision = snapshot.revision
            if changed:
                applied = workflow_model.objects.apply_definition(head, expected_revision=revision, edit=edit)
                revision = applied.revision
            saved_nodes_after = {node.key: node for node in head.steps.all()}
            saved_edges_after = {
                (edge.source.key, edge.target.key, edge.condition): edge
                for edge in head.edges.select_related("source", "target")
            }
            for _group, resource, row, wanted in declared_nodes[head_id]:
                counts[resource.record_declared_instance(row, saved_nodes_after[wanted["key"]])] += 1
            for _group, resource, row, signature in edge_rows:
                counts[resource.record_declared_instance(row, saved_edges_after[signature])] += 1
            if publications.get(head_id, False):
                workflow_model.objects.publish_definition(head, expected_revision=revision)
        return LoadResult(**counts)
    except (DefinitionEditError, DefinitionReadinessError, ValidationError, ValueError, KeyError) as error:
        raise ResourceLoadError(f"Workflow declaration installation failed: {error}") from error
