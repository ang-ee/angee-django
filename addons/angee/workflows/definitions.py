"""Atomic, transport-free workflow definition edit commands."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import ValidationError
from django.db import router

from angee.base.scoping import system_queryset
from angee.workflows.graph import GraphDiagnostic, GraphIdentity, GraphInputSource, GraphLocation, WorkflowGraph


@dataclass(frozen=True, slots=True)
class EndpointRef:
    """Reference exactly one persisted or command-created step."""

    existing_id: int | None = None
    client_key: str | None = None


@dataclass(frozen=True, slots=True)
class NodeCreate:
    client_key: str
    fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NodePatch:
    identity: int
    fields: dict[str, Any]
    requested_id: str | None = None


@dataclass(frozen=True, slots=True)
class NodeDelete:
    identity: int
    requested_id: str | None = None


@dataclass(frozen=True, slots=True)
class EdgeCreate:
    client_key: str
    source: EndpointRef
    target: EndpointRef
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EdgePatch:
    identity: int
    fields: dict[str, Any] = field(default_factory=dict)
    source: EndpointRef | None = None
    target: EndpointRef | None = None
    requested_id: str | None = None


@dataclass(frozen=True, slots=True)
class EdgeDelete:
    identity: int
    requested_id: str | None = None


@dataclass(frozen=True, slots=True)
class DefinitionEdit:
    workflow: dict[str, Any] = field(default_factory=dict)
    node_creates: tuple[NodeCreate, ...] = ()
    node_patches: tuple[NodePatch, ...] = ()
    node_deletes: tuple[NodeDelete, ...] = ()
    edge_creates: tuple[EdgeCreate, ...] = ()
    edge_patches: tuple[EdgePatch, ...] = ()
    edge_deletes: tuple[EdgeDelete, ...] = ()


@dataclass(frozen=True, slots=True)
class Correlation:
    client_key: str
    identity: int


@dataclass(frozen=True, slots=True)
class DefinitionResult:
    revision: int
    nodes: tuple[Correlation, ...]
    edges: tuple[Correlation, ...]
    readiness: tuple[GraphDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class DefinitionSnapshot:
    revision: int
    workflow: Any
    nodes: tuple[Any, ...]
    edges: tuple[Any, ...]
    readiness: tuple[GraphDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class PublicationResult:
    revision: int
    publication: Any
    created: bool


@dataclass(frozen=True, slots=True)
class DefinitionInputSources:
    revision: int
    target: GraphIdentity
    sources: tuple[GraphInputSource, ...]
    readiness: tuple[GraphDiagnostic, ...]


class StaleDefinitionError(Exception):
    """Raised when a command does not target the locked draft revision."""

    def __init__(self, *, expected: int, current: int) -> None:
        self.expected = expected
        self.current = current
        super().__init__(f"Draft revision is stale: expected {expected}, current {current}.")


class DefinitionEditError(Exception):
    """Raised with all command or structural diagnostics before persistence."""

    def __init__(self, diagnostics: tuple[GraphDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__(f"Workflow definition edit is structurally invalid: {diagnostics!r}")


class DefinitionReadinessError(Exception):
    """Raised with every readiness diagnostic blocking publication."""

    def __init__(self, diagnostics: tuple[GraphDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__(f"Workflow definition is not ready: {diagnostics!r}")


class WorkflowDefinitionManagerMixin:
    """Own atomic workflow definition commands on a Workflow manager."""

    _WORKFLOW_FIELDS = frozenset(
        {"name", "description", "purpose", "subject_declaration", "error_workflow", "max_steps", "budget"}
    )
    _NODE_FIELDS = frozenset(
        {"key", "name", "step_class", "config", "input_binding", "join_rule", "is_entry", "position"}
    )
    _EDGE_FIELDS = frozenset({"condition"})

    def definition_snapshot(self, workflow: Any) -> DefinitionSnapshot:
        """Read one coherent revision and definition under its lineage lock."""

        with self._definition_caller(workflow):
            return self._definition_snapshot(workflow)

    def _definition_snapshot(self, workflow: Any) -> DefinitionSnapshot:
        alias = router.db_for_write(self.model, instance=workflow)
        self.using(alias).with_action("read").get(pk=workflow.pk)
        with self._definition_read(workflow.pk, using=alias) as locked:
            projected = self.using(alias).with_action("read").with_lineage_projection().get(pk=locked.pk)
            nodes = tuple(locked.steps.order_by("key", "pk"))
            edges = tuple(locked.edges.select_related("source", "target").order_by("pk"))
            step_model = locked.steps.model
            edge_model = locked.edges.model
            if (
                len(nodes) != system_queryset(step_model, using=alias).filter(workflow_id=locked.pk).count()
                or len(edges) != system_queryset(edge_model, using=alias).filter(workflow_id=locked.pk).count()
            ):
                raise DefinitionEditError(
                    (
                        GraphDiagnostic(
                            "reference_invalid",
                            "Definition rows are missing or unavailable.",
                            GraphLocation("workflow", GraphIdentity(existing_id=locked.pk), "definition"),
                        ),
                    )
                )
            readiness = WorkflowGraph.from_rows(locked, nodes, edges).diagnostics()
            return DefinitionSnapshot(locked.draft_revision, projected, nodes, edges, readiness)

    def apply_definition(
        self,
        workflow: Any,
        *,
        expected_revision: int,
        edit: DefinitionEdit,
    ) -> DefinitionResult:
        """Apply one compare-and-swap edit and return its committed revision."""

        self._validate_expected_revision(workflow, expected_revision)
        with self._definition_caller(workflow):
            return self._apply_definition(workflow, expected_revision=expected_revision, edit=edit)

    def definition_input_sources(
        self,
        workflow: Any,
        *,
        expected_revision: int,
        edit: DefinitionEdit,
        target: EndpointRef,
    ) -> DefinitionInputSources:
        """Project source metadata from one unsaved definition without persisting it."""

        self._validate_expected_revision(workflow, expected_revision)
        with self._definition_caller(workflow):
            alias = router.db_for_write(self.model, instance=workflow)
            with self._definition_read(workflow.pk, using=alias) as locked:
                if locked.draft_revision != expected_revision:
                    raise StaleDefinitionError(expected=expected_revision, current=locked.draft_revision)
                state = _DefinitionState(self, locked, edit, alias=alias, action="read")
                state.preflight()
                target_identity = state.target_identity(target)
                graph = state.graph()
                return DefinitionInputSources(
                    locked.draft_revision,
                    target_identity,
                    graph.input_sources(target_identity),
                    graph.diagnostics(),
                )

    def publish_definition(self, workflow: Any, *, expected_revision: int) -> PublicationResult:
        """Publish the exact saved revision or return its identical publication."""

        self._validate_expected_revision(workflow, expected_revision)
        with self._definition_caller(workflow):
            alias = router.db_for_write(self.model, instance=workflow)
            with self._definition_write((workflow.pk,), using=alias):
                draft = self.using(alias).get(pk=workflow.pk)
                if draft.draft_revision != expected_revision:
                    raise StaleDefinitionError(expected=expected_revision, current=draft.draft_revision)
                diagnostics = WorkflowGraph.from_workflow(draft).diagnostics()
                if diagnostics:
                    raise DefinitionReadinessError(diagnostics)
                current = self.current_published_for(draft)
                if current is not None and draft._definition_signature() == current._definition_signature():
                    projected = self.using(alias).with_action("read").with_lineage_projection().get(pk=current.pk)
                    return PublicationResult(draft.draft_revision, projected, False)
                published = draft.publish()
                projected = self.using(alias).with_action("read").with_lineage_projection().get(pk=published.pk)
                return PublicationResult(draft.draft_revision, projected, True)

    @staticmethod
    def _validate_expected_revision(workflow: Any, expected_revision: int) -> None:
        if type(expected_revision) is int and 0 <= expected_revision <= 2_147_483_647:
            return
        raise DefinitionEditError(
            (
                GraphDiagnostic(
                    "revision_invalid",
                    "Expected revision must be a non-negative 32-bit integer.",
                    GraphLocation("workflow", GraphIdentity(existing_id=workflow.pk), "draft_revision"),
                ),
            )
        )

    def _apply_definition(self, workflow: Any, *, expected_revision: int, edit: DefinitionEdit) -> DefinitionResult:
        alias = router.db_for_write(self.model, instance=workflow)
        result: DefinitionResult
        with self._definition_write((workflow.pk,), using=alias):
            locked = self.using(alias).get(pk=workflow.pk)
            if locked.draft_revision != expected_revision:
                raise StaleDefinitionError(expected=expected_revision, current=locked.draft_revision)
            state = _DefinitionState(self, locked, edit, alias=alias)
            state.preflight()
            node_correlations, edge_correlations = state.persist()
            revision = self._definition_revision(locked.pk, locked.draft_revision)
            readiness = WorkflowGraph.from_workflow(locked).diagnostics()
            result = DefinitionResult(revision, tuple(node_correlations), tuple(edge_correlations), readiness)
        return result


class _DefinitionState:
    """One proposed definition inside its manager-owned locked transaction."""

    def __init__(self, manager: Any, workflow: Any, edit: DefinitionEdit, *, alias: str, action: str = "write") -> None:
        self.manager = manager
        self.workflow = workflow
        self.edit = edit
        self.alias = alias
        self.step_model = workflow.steps.model
        self.edge_model = workflow.edges.model
        self.workflow_fields = dict(edit.workflow)
        self.nodes = {row.pk: row for row in workflow.steps.with_action(action).order_by("pk")}
        self.edges = {
            row.pk: row for row in workflow.edges.with_action(action).select_related("source", "target").order_by("pk")
        }
        self.original_nodes = dict(self.nodes)
        self.original_edges = dict(self.edges)
        self.original_edge_signatures = {identity: _edge_signature(row) for identity, row in self.edges.items()}
        self.created_nodes: dict[str, Any] = {}
        self.created_edges: dict[str, Any] = {}
        self.diagnostics: list[GraphDiagnostic] = []

    def preflight(self) -> None:
        all_node_count = system_queryset(self.step_model, using=self.alias).filter(workflow_id=self.workflow.pk).count()
        all_edge_count = system_queryset(self.edge_model, using=self.alias).filter(workflow_id=self.workflow.pk).count()
        if len(self.nodes) != all_node_count or len(self.edges) != all_edge_count:
            self._command(
                "workflow",
                GraphIdentity(existing_id=self.workflow.pk),
                "definition",
                "reference_invalid",
                "Definition rows are missing or unavailable.",
            )
        self._validate_command_shape()
        self._validate_workflow_relations()
        if self.diagnostics:
            raise DefinitionEditError(tuple(self.diagnostics))
        self._build_proposed_rows()
        self._validate_constraint_order()
        self._validate_models()
        graph = self.graph()
        self.diagnostics.extend(graph.structural_diagnostics())
        if self.diagnostics:
            raise DefinitionEditError(tuple(self.diagnostics))

    def graph(self) -> WorkflowGraph:
        """Return the one graph projection shared by preview and persistence preflight."""

        return WorkflowGraph.from_rows(
            self.workflow,
            self.nodes.values(),
            self.edges.values(),
            identity=lambda row: getattr(row, "_definition_identity", GraphIdentity(existing_id=row.pk)),
            owner_identity=lambda row: GraphIdentity(existing_id=row.workflow_id),
        )

    def target_identity(self, reference: EndpointRef) -> GraphIdentity:
        exact = (reference.existing_id is None) != (reference.client_key is None)
        if not exact:
            raise DefinitionEditError(
                (
                    GraphDiagnostic(
                        "reference_invalid",
                        "Target is missing or unavailable.",
                        GraphLocation("node", GraphIdentity(), "identity"),
                    ),
                )
            )
        if reference.existing_id is not None and reference.existing_id in self.nodes:
            return GraphIdentity(existing_id=reference.existing_id)
        if reference.client_key is not None and reference.client_key in self.created_nodes:
            return GraphIdentity(client_key=reference.client_key)
        identity = GraphIdentity(existing_id=reference.existing_id, client_key=reference.client_key)
        raise DefinitionEditError(
            (
                GraphDiagnostic(
                    "reference_invalid",
                    "Target is missing or unavailable.",
                    GraphLocation("node", identity, "identity"),
                ),
            )
        )

    def _validate_workflow_relations(self) -> None:
        if "error_workflow" not in self.workflow_fields or self.workflow_fields["error_workflow"] is None:
            return
        related = self.workflow_fields["error_workflow"]
        related_pk = getattr(related, "pk", None)
        available = self.manager.using(self.alias).with_action("read").filter(pk=related_pk).first()
        if available is None:
            self._command(
                "workflow",
                GraphIdentity(existing_id=self.workflow.pk),
                "error_workflow",
                "reference_invalid",
                "Error workflow is missing or unavailable.",
            )
            return
        self.workflow_fields["error_workflow"] = available

    def _validate_constraint_order(self) -> None:
        """Reject existing edge swaps that immediate uniqueness cannot persist."""

        original = {signature: identity for identity, signature in self.original_edge_signatures.items()}
        proposed = {identity: _edge_signature(row) for identity, row in self.edges.items() if isinstance(identity, int)}
        for identity, signature in proposed.items():
            owner = original.get(signature)
            if owner is not None and owner != identity and proposed.get(owner) != signature:
                self._command(
                    "edge",
                    GraphIdentity(existing_id=identity),
                    "identity",
                    "edge_swap_unsupported",
                    "Atomic swaps of existing edge identities are not supported.",
                )

    def persist(self) -> tuple[list[Correlation], list[Correlation]]:
        workflow_fields = set(self.workflow_fields)
        if workflow_fields:
            for name, value in self.workflow_fields.items():
                setattr(self.workflow, name, copy.deepcopy(value))
            self.workflow.save(update_fields=workflow_fields)

        deleted_node_ids = {item.identity for item in self.edit.node_deletes}
        incident = [
            edge
            for edge in self.original_edges.values()
            if edge.source_id in deleted_node_ids or edge.target_id in deleted_node_ids
        ]
        for edge in sorted(incident, key=lambda row: row.pk or 0):
            if edge.pk:
                edge.delete()
        for item in self.edit.edge_deletes:
            edge = self.original_edges.get(item.identity)
            if edge is not None and edge not in incident:
                edge.delete()
        for item in self.edit.node_deletes:
            self.original_nodes[item.identity].delete()

        node_results: list[Correlation] = []
        for item in self.edit.node_patches:
            row = self.nodes[item.identity]
            row.save(update_fields=set(item.fields))
        for item in self.edit.node_creates:
            row = self.created_nodes[item.client_key]
            row.save()
            node_results.append(Correlation(item.client_key, row.pk))

        edge_results: list[Correlation] = []
        for item in self.edit.edge_patches:
            row = self.edges[item.identity]
            fields = set(item.fields)
            if item.source is not None:
                row.source = self._persisted_endpoint(item.source)
                fields.add("source")
            if item.target is not None:
                row.target = self._persisted_endpoint(item.target)
                fields.add("target")
            row.save(update_fields=fields)
        for item in self.edit.edge_creates:
            row = self.created_edges[item.client_key]
            row.source = self._persisted_endpoint(item.source)
            row.target = self._persisted_endpoint(item.target)
            row.save()
            edge_results.append(Correlation(item.client_key, row.pk))
        return node_results, edge_results

    def _validate_command_shape(self) -> None:
        self._fields(
            "workflow",
            GraphIdentity(existing_id=self.workflow.pk),
            self.workflow_fields,
            WorkflowDefinitionManagerMixin._WORKFLOW_FIELDS,
        )
        for item in self.edit.node_creates:
            self._fields("node", GraphIdentity(client_key=item.client_key), item.fields, self.manager._NODE_FIELDS)
        for item in self.edit.node_patches:
            self._fields("node", _edit_identity(item), item.fields, self.manager._NODE_FIELDS)
        for item in self.edit.edge_creates:
            self._fields("edge", GraphIdentity(client_key=item.client_key), item.fields, self.manager._EDGE_FIELDS)
        for item in self.edit.edge_patches:
            self._fields("edge", _edit_identity(item), item.fields, self.manager._EDGE_FIELDS)
        self._unique("node", "client_key", [item.client_key for item in self.edit.node_creates], client=True)
        self._unique("edge", "client_key", [item.client_key for item in self.edit.edge_creates], client=True)
        self._unique(
            "workflow",
            "client_key",
            [item.client_key for item in (*self.edit.node_creates, *self.edit.edge_creates)],
            client=True,
        )
        self._unique("node", "identity", [item.identity for item in (*self.edit.node_patches, *self.edit.node_deletes)])
        self._unique("edge", "identity", [item.identity for item in (*self.edit.edge_patches, *self.edit.edge_deletes)])
        for item in (*self.edit.node_patches, *self.edit.node_deletes):
            if item.identity not in self.nodes:
                self._command(
                    "node", _edit_identity(item), "identity", "reference_invalid", "Node is missing or unavailable."
                )
        for item in (*self.edit.edge_patches, *self.edit.edge_deletes):
            if item.identity not in self.edges:
                self._command(
                    "edge", _edit_identity(item), "identity", "reference_invalid", "Edge is missing or unavailable."
                )
        key_patches = {item.identity: item.fields["key"] for item in self.edit.node_patches if "key" in item.fields}
        key_owners = {row.key: identity for identity, row in self.nodes.items()}
        for identity, desired in key_patches.items():
            owner = key_owners.get(desired) if isinstance(desired, str) else None
            if owner is not None and owner != identity and owner in key_patches:
                self._command(
                    "node",
                    GraphIdentity(existing_id=identity),
                    "key",
                    "key_swap_unsupported",
                    "Atomic swaps of existing step keys are not supported.",
                )
        deleted_nodes = {item.identity for item in self.edit.node_deletes}
        incident_edges = {
            identity
            for identity, edge in self.edges.items()
            if edge.source_id in deleted_nodes or edge.target_id in deleted_nodes
        }
        for item in self.edit.edge_patches:
            if item.identity in incident_edges:
                self._command(
                    "edge",
                    GraphIdentity(existing_id=item.identity),
                    "identity",
                    "edit_conflict",
                    "An edge removed with its node cannot also be patched.",
                )
        for item in (*self.edit.edge_creates, *self.edit.edge_patches):
            for field_name, reference in (("source", item.source), ("target", item.target)):
                if reference is not None:
                    self._validate_endpoint(
                        reference,
                        location=(
                            GraphIdentity(client_key=item.client_key)
                            if hasattr(item, "client_key")
                            else _edit_identity(item)
                        ),
                        field=field_name,
                    )

    def _build_proposed_rows(self) -> None:
        for name, value in self.workflow_fields.items():
            setattr(self.workflow, name, copy.deepcopy(value))
        for item in self.edit.node_patches:
            row = self.nodes[item.identity]
            for name, value in item.fields.items():
                setattr(row, name, copy.deepcopy(value))
        for item in self.edit.node_deletes:
            self.nodes.pop(item.identity)
        for item in self.edit.node_creates:
            row = self.step_model(workflow=self.workflow, **copy.deepcopy(item.fields))
            row._definition_identity = GraphIdentity(client_key=item.client_key)
            self.created_nodes[item.client_key] = row
            self.nodes[item.client_key] = row
        for row in self.nodes.values():
            row._definition_identity = getattr(row, "_definition_identity", GraphIdentity(existing_id=row.pk))
        deleted_nodes = {item.identity for item in self.edit.node_deletes}
        for identity, row in tuple(self.edges.items()):
            if (
                identity in {item.identity for item in self.edit.edge_deletes}
                or row.source_id in deleted_nodes
                or row.target_id in deleted_nodes
            ):
                self.edges.pop(identity)
        for item in self.edit.edge_patches:
            row = self.edges[item.identity]
            for name, value in item.fields.items():
                setattr(row, name, copy.deepcopy(value))
            if item.source is not None:
                row.source = self._endpoint(item.source)
            if item.target is not None:
                row.target = self._endpoint(item.target)
        for item in self.edit.edge_creates:
            row = self.edge_model(
                workflow=self.workflow,
                source=self._endpoint(item.source),
                target=self._endpoint(item.target),
                **copy.deepcopy(item.fields),
            )
            row._definition_identity = GraphIdentity(client_key=item.client_key)
            self.created_edges[item.client_key] = row
            self.edges[item.client_key] = row
        for row in self.edges.values():
            row._definition_identity = getattr(row, "_definition_identity", GraphIdentity(existing_id=row.pk))

    def _validate_models(self) -> None:
        try:
            self.workflow.full_clean(validate_unique=False, validate_constraints=False)
        except ValidationError as error:
            for path, messages in error.message_dict.items():
                for message in messages:
                    self._command(
                        "workflow",
                        GraphIdentity(existing_id=self.workflow.pk),
                        path,
                        "field_invalid",
                        message,
                    )
        for kind, rows in (("node", self.nodes.values()), ("edge", self.edges.values())):
            for row in rows:
                try:
                    row.full_clean(
                        exclude={"source", "target"} if kind == "edge" else None,
                        validate_unique=False,
                        validate_constraints=False,
                    )
                except ValidationError as error:
                    identity = row._definition_identity
                    for path, messages in error.message_dict.items():
                        for message in messages:
                            self._command(kind, identity, path, "field_invalid", message)

    def _endpoint(self, reference: EndpointRef) -> Any:
        return (
            self.nodes[reference.existing_id]
            if reference.existing_id is not None
            else self.created_nodes[reference.client_key]
        )

    def _persisted_endpoint(self, reference: EndpointRef) -> Any:
        return self._endpoint(reference)

    def _validate_endpoint(self, reference: EndpointRef, *, location: GraphIdentity, field: str) -> None:
        exact = (reference.existing_id is None) != (reference.client_key is None)
        deleted = {item.identity for item in self.edit.node_deletes}
        found = (
            reference.existing_id in self.nodes and reference.existing_id not in deleted
            if reference.existing_id is not None
            else reference.client_key in {item.client_key for item in self.edit.node_creates}
        )
        if not exact or not found:
            self._command("edge", location, field, "reference_invalid", "Endpoint is missing or unavailable.")

    def _fields(
        self,
        kind: str,
        identity: GraphIdentity,
        values: dict[str, Any],
        allowed: frozenset[str],
    ) -> None:
        model = {"workflow": self.workflow._meta.model, "node": self.step_model, "edge": self.edge_model}[kind]
        for name in sorted(set(values) - allowed):
            self._command(
                kind,
                identity,
                name,
                "field_unsupported",
                f"Field {name!r} cannot be edited.",
            )
        for name, value in values.items():
            if name in allowed and value is None and not model._meta.get_field(name).null:
                self._command(
                    kind,
                    identity,
                    name,
                    "field_invalid",
                    "This field cannot be null.",
                )

    def _unique(self, kind: str, field: str, values: list[Any], *, client: bool = False) -> None:
        seen: set[Any] = set()
        for value in values:
            if not value or value in seen:
                self._command(
                    kind,
                    GraphIdentity(client_key=str(value)) if client else GraphIdentity(existing_id=value),
                    field,
                    "identity_duplicate",
                    f"{field} must be request-unique and non-blank.",
                )
            seen.add(value)

    def _command(self, kind: str, identity: GraphIdentity, field: str, code: str, message: str) -> None:
        self.diagnostics.append(GraphDiagnostic(code, message, GraphLocation(kind, identity, field)))


def _edge_signature(edge: Any) -> tuple[GraphIdentity, GraphIdentity, str]:
    """Return the domain identity tuple behind the immediate edge constraint."""

    source = getattr(edge.source, "_definition_identity", GraphIdentity(existing_id=edge.source_id))
    target = getattr(edge.target, "_definition_identity", GraphIdentity(existing_id=edge.target_id))
    return source, target, str(edge.condition)


def _edit_identity(item: NodePatch | NodeDelete | EdgePatch | EdgeDelete) -> GraphIdentity:
    return GraphIdentity(
        existing_id=item.identity if item.identity >= 0 else None,
        requested_id=item.requested_id,
    )
