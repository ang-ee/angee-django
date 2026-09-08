"""Immutable workflow-definition graph and readiness diagnostics."""

from __future__ import annotations

import copy
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal, cast

from django.core.exceptions import ImproperlyConfigured, ValidationError

from angee.workflows.steps import StepImpl, validate_retry_config


@dataclass(frozen=True, slots=True)
class GraphIdentity:
    """Transport-neutral persisted or request-local graph identity."""

    existing_id: int | None = None
    client_key: str | None = None
    requested_id: str | None = None

    def __str__(self) -> str:
        if self.client_key is not None:
            return self.client_key
        if self.existing_id is not None:
            return str(self.existing_id)
        return self.requested_id or "unknown"


@dataclass(frozen=True, slots=True)
class GraphLocation:
    kind: Literal["workflow", "node", "edge"]
    key: GraphIdentity
    field: str

    @property
    def path(self) -> str:
        return ".".join(str(part) for part in (self.kind, self.key, self.field) if part)


@dataclass(frozen=True, slots=True)
class GraphDiagnostic:
    code: str
    message: str
    location: GraphLocation


@dataclass(frozen=True, slots=True)
class GraphNode:
    identity: GraphIdentity
    workflow_identity: GraphIdentity
    key: str
    is_entry: bool
    operation_key: str
    impl: type[StepImpl] | None
    config: Any


@dataclass(frozen=True, slots=True)
class GraphEdge:
    identity: GraphIdentity
    workflow_identity: GraphIdentity
    source_identity: GraphIdentity
    target_identity: GraphIdentity
    source_key: str
    target_key: str
    condition: str


@dataclass(frozen=True, slots=True)
class WorkflowGraph:
    """One immutable validation view of persisted or proposed workflow rows."""

    identity: GraphIdentity
    max_steps: int
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    @classmethod
    def from_workflow(cls, workflow: Any) -> WorkflowGraph:
        return cls.from_rows(
            workflow,
            workflow.steps.order_by("key", "pk"),
            workflow.edges.select_related("source", "target").order_by("pk"),
        )

    @classmethod
    def from_rows(
        cls,
        workflow: Any,
        steps: Iterable[Any],
        edges: Iterable[Any],
        *,
        identity: Callable[[Any], GraphIdentity] = lambda row: GraphIdentity(existing_id=row.pk),
        owner_identity: Callable[[Any], GraphIdentity] = lambda row: GraphIdentity(existing_id=row.workflow_id),
    ) -> WorkflowGraph:
        """Capture rows using an explicit identity adapter for prospective edits."""

        workflow_identity = identity(workflow)
        nodes: list[GraphNode] = []
        for step in steps:
            try:
                impl = cast(type[StepImpl], step.resolve_impl("step_class"))
            except ImproperlyConfigured:
                impl = None
            nodes.append(
                GraphNode(
                    identity(step),
                    owner_identity(step),
                    str(step.key),
                    bool(step.is_entry),
                    str(step.step_class),
                    impl,
                    copy.deepcopy(step.config),
                )
            )
        graph_edges = tuple(
            GraphEdge(
                identity(edge),
                owner_identity(edge),
                identity(edge.source),
                identity(edge.target),
                str(edge.source.key),
                str(edge.target.key),
                str(edge.condition),
            )
            for edge in edges
        )
        max_steps = workflow.max_steps if type(workflow.max_steps) is int else 0
        return cls(workflow_identity, max_steps, tuple(nodes), graph_edges)

    def diagnostics(self) -> tuple[GraphDiagnostic, ...]:
        diagnostics = list(self.structural_diagnostics())
        diagnostics.extend(self._operations())
        map_targets, map_errors = self._maps()
        diagnostics.extend(map_errors)
        diagnostics.extend(self._routing(map_targets))
        diagnostics.extend(self._capacity(map_targets))
        return tuple(diagnostics)

    def structural_diagnostics(self) -> tuple[GraphDiagnostic, ...]:
        """Return persistence blockers without applying readiness policy."""

        return tuple(self._structural())

    def validate(self) -> None:
        errors: dict[str, list[str]] = defaultdict(list)
        for diagnostic in self.diagnostics():
            errors[diagnostic.location.path].append(diagnostic.message)
        if errors:
            raise ValidationError(dict(errors))

    def _structural(self) -> list[GraphDiagnostic]:
        result: list[GraphDiagnostic] = []
        identities = {node.identity for node in self.nodes}
        identity_counts = Counter(node.identity for node in self.nodes)
        keys = Counter(node.key for node in self.nodes)
        for node in self.nodes:
            if identity_counts[node.identity] > 1:
                result.append(self._node(node, "node_identity_duplicate", "Step identity is duplicated.", "identity"))
            if not node.key.strip():
                result.append(self._node(node, "node_key_blank", "Step key must be non-blank.", "key"))
            elif keys[node.key] > 1:
                result.append(self._node(node, "node_key_duplicate", f"Step key {node.key!r} is duplicated.", "key"))
            if node.workflow_identity != self.identity:
                result.append(
                    self._node(node, "node_workflow_mismatch", "Step belongs to another workflow.", "workflow")
                )
            if node.impl is None:
                message = f"Operation {node.operation_key!r} is not registered."
                result.append(self._node(node, "operation_unknown", message, "step_class"))
        edge_identities = Counter(edge.identity for edge in self.edges)
        duplicates = Counter((edge.source_identity, edge.target_identity, edge.condition) for edge in self.edges)
        for edge in self.edges:
            if edge_identities[edge.identity] > 1:
                result.append(self._edge(edge, "edge_identity_duplicate", "Edge identity is duplicated.", "identity"))
            if edge.workflow_identity != self.identity:
                result.append(
                    self._edge(edge, "edge_workflow_mismatch", "Edge belongs to another workflow.", "workflow")
                )
            if edge.source_identity not in identities:
                result.append(self._edge(edge, "edge_source_missing", "Edge source is not in this workflow.", "source"))
            if edge.target_identity not in identities:
                result.append(self._edge(edge, "edge_target_missing", "Edge target is not in this workflow.", "target"))
            if duplicates[(edge.source_identity, edge.target_identity, edge.condition)] > 1:
                result.append(
                    self._edge(edge, "edge_duplicate", "Edge source, target and condition are duplicated.", "condition")
                )
        return result

    def _operations(self) -> list[GraphDiagnostic]:
        result: list[GraphDiagnostic] = []
        if sum(node.is_entry for node in self.nodes) != 1:
            result.append(self._workflow("entry_count", "A workflow must have exactly one entry step.", "steps"))
        for node in self.nodes:
            if node.impl is None:
                continue
            if not node.impl.is_executable(registered_key=node.operation_key):
                result.append(
                    self._node(node, "operation_not_executable", "Operation cannot be executed.", "step_class")
                )
            result.extend(self._config(node))
        return result

    def _config(self, node: GraphNode) -> list[GraphDiagnostic]:
        assert node.impl is not None
        errors: list[GraphDiagnostic] = []
        validators = [node.impl.validate_config]
        if node.impl.config_model is None:
            validators.append(validate_retry_config)
        for validator in validators:
            try:
                validator(node.config)
            except ValidationError as error:
                errors.extend(
                    self._node(node, "config_invalid", message, path)
                    for path, messages in error.message_dict.items()
                    for message in messages
                )
        return errors

    def _maps(self) -> tuple[dict[GraphIdentity, list[GraphNode]], list[GraphDiagnostic]]:
        result: list[GraphDiagnostic] = []
        keys = Counter(node.key for node in self.nodes)
        unique = {node.key: node for node in self.nodes if keys[node.key] == 1}
        targets: dict[str, list[GraphNode]] = defaultdict(list)
        for node in self.nodes:
            target = node.impl.map_body_target(node.config) if node.impl else None
            if target is None:
                continue
            body = unique.get(target)
            if body is None:
                message = f"Map target {target!r} is not unique in this workflow."
                result.append(self._node(node, "map_target_missing", message, "config.target_step"))
                continue
            targets[body.identity].append(node)
            if body.identity == node.identity:
                result.append(self._node(node, "map_target_self", "A Map cannot target itself.", "config.target_step"))
            if body.impl and body.impl.map_body_operation:
                message = "A Map body cannot itself be a Map."
                result.append(self._node(node, "map_target_nested", message, "config.target_step"))
        by_id = {node.identity: node for node in self.nodes}
        for body_id, owners in targets.items():
            body = by_id[body_id]
            if len(owners) > 1:
                for owner in owners:
                    message = f"Map body {body.key!r} has more than one owner."
                    result.append(self._node(owner, "map_target_shared", message, "config.target_step"))
            if body.is_entry:
                result.append(self._node(body, "map_body_entry", "A Map body cannot be an entry step.", "is_entry"))
        return targets, result

    def _routing(self, map_targets: dict[GraphIdentity, list[GraphNode]]) -> list[GraphDiagnostic]:
        result: list[GraphDiagnostic] = []
        by_id = {node.identity: node for node in self.nodes}
        bodies = set(map_targets)
        edges = [edge for edge in self.edges if edge.source_identity in by_id and edge.target_identity in by_id]
        for edge in edges:
            if edge.source_identity in bodies:
                result.append(
                    self._edge(edge, "map_body_edge", "A Map body cannot have ordinary outgoing edges.", "source")
                )
            if edge.target_identity in bodies:
                result.append(
                    self._edge(edge, "map_body_edge", "A Map body cannot have ordinary incoming edges.", "target")
                )
            source = by_id[edge.source_identity]
            if source.impl and edge.condition and source.impl.outcomes:
                if edge.condition not in {outcome.key for outcome in source.impl.outcomes}:
                    message = f"Outcome {edge.condition!r} is not declared by {source.key!r}."
                    result.append(self._edge(edge, "outcome_unsupported", message, "condition"))
        ordinary = set(by_id) - bodies
        ordinary_edges = [
            edge for edge in edges if edge.source_identity in ordinary and edge.target_identity in ordinary
        ]
        result.extend(self._cycles(ordinary, ordinary_edges, by_id))
        entries = [node for node in self.nodes if node.is_entry]
        if len(entries) == 1 and entries[0].identity in ordinary:
            reached = _reachable(entries[0].identity, ordinary_edges)
            for identity in sorted(ordinary - reached, key=_identity_order):
                result.append(
                    self._node(by_id[identity], "unreachable", "Step is not reachable from the entry step.", "key")
                )
        return result

    def _cycles(
        self,
        nodes: set[GraphIdentity],
        edges: list[GraphEdge],
        by_id: dict[GraphIdentity, GraphNode],
    ) -> list[GraphDiagnostic]:
        predecessors: dict[GraphIdentity, set[GraphIdentity]] = {identity: set() for identity in nodes}
        for edge in edges:
            predecessors[edge.target_identity].add(edge.source_identity)
        try:
            tuple(TopologicalSorter(predecessors).static_order())
        except CycleError as error:
            cycle = tuple(dict.fromkeys(cast(list[GraphIdentity], error.args[1])))
            return [
                self._node(by_id[identity], "cycle", "Step participates in an ordinary edge cycle.", "key")
                for identity in cycle
            ]
        return []

    def _capacity(self, map_targets: dict[GraphIdentity, list[GraphNode]]) -> list[GraphDiagnostic]:
        result: list[GraphDiagnostic] = []
        for owners in map_targets.values():
            for node in owners:
                items = node.config.get("items") if isinstance(node.config, dict) else None
                if isinstance(items, list) and 1 + len(items) > self.max_steps:
                    message = f"Map and literal children exceed workflow max_steps={self.max_steps}."
                    result.append(self._node(node, "map_capacity", message, "config.items"))
        return result

    def _workflow(self, code: str, message: str, field: str) -> GraphDiagnostic:
        return GraphDiagnostic(code, message, GraphLocation("workflow", self.identity, field))

    @staticmethod
    def _node(node: GraphNode, code: str, message: str, field: str) -> GraphDiagnostic:
        return GraphDiagnostic(code, message, GraphLocation("node", node.identity, field))

    @staticmethod
    def _edge(edge: GraphEdge, code: str, message: str, field: str) -> GraphDiagnostic:
        return GraphDiagnostic(code, message, GraphLocation("edge", edge.identity, field))


def _reachable(entry: GraphIdentity, edges: list[GraphEdge]) -> set[GraphIdentity]:
    outgoing: dict[GraphIdentity, list[GraphIdentity]] = defaultdict(list)
    for edge in edges:
        outgoing[edge.source_identity].append(edge.target_identity)
    reached = {entry}
    pending = deque([entry])
    while pending:
        for target in outgoing[pending.popleft()]:
            if target not in reached:
                reached.add(target)
                pending.append(target)
    return reached


def _identity_order(identity: GraphIdentity) -> tuple[bool, int, str, str]:
    return (
        identity.client_key is not None,
        identity.existing_id or 0,
        identity.client_key or "",
        identity.requested_id or "",
    )
