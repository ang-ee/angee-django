"""Immutable workflow-definition graph and readiness diagnostics."""

from __future__ import annotations

import copy
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal, cast

from django.core.exceptions import ImproperlyConfigured, ValidationError
from pydantic import ValidationError as PydanticValidationError

from angee.workflows.bindings import binding_error_details, parse_binding
from angee.workflows.data_contracts import DataContract, model_data_contract
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
    detail_path: tuple[str | int, ...] = ()

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
    name: str = ""
    input_binding: Any = None


@dataclass(frozen=True, slots=True)
class GraphInputSource:
    kind: Literal["workflow_input", "step_output", "map_item"]
    contract: DataContract
    node_identity: GraphIdentity | None = None
    step_key: str | None = None
    label: str | None = None


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
                    identity=identity(step),
                    workflow_identity=owner_identity(step),
                    key=str(step.key),
                    is_entry=bool(step.is_entry),
                    operation_key=str(step.step_class),
                    impl=impl,
                    config=copy.deepcopy(step.config),
                    name=str(getattr(step, "name", step.key)),
                    input_binding=copy.deepcopy(getattr(step, "input_binding", None)),
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
        diagnostics.extend(self._bindings(map_targets))
        return tuple(diagnostics)

    def input_sources(self, target_identity: GraphIdentity) -> tuple[GraphInputSource, ...]:
        """Return declared sources allowed by this exact prospective graph."""

        by_id = {node.identity: node for node in self.nodes}
        unique_keys = {key for key, count in Counter(node.key for node in self.nodes).items() if count == 1}
        target = by_id.get(target_identity)
        if target is None:
            return ()
        map_targets, _ = self._maps()
        eligible: set[GraphIdentity] = set()
        include_map_item = False
        owners = map_targets.get(target.identity, ())
        if (
            len(owners) == 1
            and owners[0].identity != target.identity
            and not target.is_entry
            and not (target.impl and target.impl.map_body_operation)
        ):
            eligible = self._ordinary_ancestors(owners[0].identity, map_targets)
            include_map_item = True
        elif target.identity not in map_targets:
            eligible = self._ordinary_ancestors(target.identity, map_targets)

        sources = [
            GraphInputSource(
                "workflow_input",
                model_data_contract(None, mode="validation"),
                label="Workflow input",
            )
        ]
        sources.extend(
            GraphInputSource(
                "step_output",
                source.impl.output_contract() if source.impl else model_data_contract(None, mode="serialization"),
                node_identity=source.identity,
                step_key=source.key,
                label=source.name,
            )
            for source in sorted(
                (by_id[identity] for identity in eligible if identity in by_id and by_id[identity].key in unique_keys),
                key=lambda node: (node.key, str(node.identity)),
            )
        )
        if include_map_item:
            sources.append(
                GraphInputSource(
                    "map_item",
                    model_data_contract(None, mode="serialization"),
                    label="Current Map item",
                )
            )
        return tuple(sources)

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
        targets: dict[GraphIdentity, list[GraphNode]] = defaultdict(list)
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

    def _ordinary_ancestors(
        self, target: GraphIdentity, map_targets: dict[GraphIdentity, list[GraphNode]]
    ) -> set[GraphIdentity]:
        bodies = set(map_targets)
        incoming: dict[GraphIdentity, list[GraphIdentity]] = defaultdict(list)
        for edge in self.edges:
            if edge.source_identity not in bodies and edge.target_identity not in bodies:
                incoming[edge.target_identity].append(edge.source_identity)
        ancestors: set[GraphIdentity] = set()
        pending = deque(incoming[target])
        while pending:
            source = pending.popleft()
            if source not in ancestors:
                ancestors.add(source)
                pending.extend(incoming[source])
        ancestors.discard(target)
        return ancestors

    def _bindings(self, map_targets: dict[GraphIdentity, list[GraphNode]]) -> list[GraphDiagnostic]:
        result: list[GraphDiagnostic] = []
        for node in self.nodes:
            if node.input_binding is None or node.impl is None:
                continue
            try:
                binding = parse_binding(node.input_binding)
            except PydanticValidationError as error:
                for detail, message in binding_error_details(node.input_binding, error):
                    result.append(self._binding(node, "binding_invalid", message, detail))
                continue
            sources = self.input_sources(node.identity)
            by_step_key: dict[str | None, list[GraphInputSource]] = defaultdict(list)
            for source in sources:
                if source.kind == "step_output":
                    by_step_key[source.step_key].append(source)
            by_kind = {source.kind: source for source in sources if source.kind != "step_output"}
            for visit in binding.visits():
                reference = visit.binding.source_reference()
                if reference is None:
                    continue
                if reference.kind == "step_output":
                    matches = by_step_key.get(reference.step_key, [])
                    selected_source = matches[0] if len(matches) == 1 else None
                else:
                    selected_source = by_kind.get(reference.kind)
                if selected_source is None:
                    result.append(
                        self._binding(
                            node,
                            "binding_source_unavailable",
                            "The referenced source is unavailable at this step.",
                            visit.binding_path,
                        )
                    )
                elif not selected_source.contract.matches_path(reference.path):
                    result.append(
                        self._binding(
                            node,
                            "binding_source_path",
                            "The path is outside the source's declared output.",
                            (*visit.binding_path, "path"),
                        )
                    )
        return result

    def _workflow(self, code: str, message: str, field: str) -> GraphDiagnostic:
        return GraphDiagnostic(code, message, GraphLocation("workflow", self.identity, field))

    @staticmethod
    def _node(node: GraphNode, code: str, message: str, field: str) -> GraphDiagnostic:
        return GraphDiagnostic(code, message, GraphLocation("node", node.identity, field))

    @staticmethod
    def _binding(node: GraphNode, code: str, message: str, detail_path: tuple[str | int, ...]) -> GraphDiagnostic:
        return GraphDiagnostic(code, message, GraphLocation("node", node.identity, "input_binding", detail_path))

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
