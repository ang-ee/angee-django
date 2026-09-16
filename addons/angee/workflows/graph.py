"""Immutable workflow-definition graph and readiness diagnostics."""

from __future__ import annotations

import copy
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal, TypeAlias, cast

from django.core.exceptions import ImproperlyConfigured, ValidationError
from jsonschema import Draft202012Validator
from pydantic import ValidationError as PydanticValidationError

from angee.workflows.attempts import json_values_equal
from angee.workflows.bindings import binding_error_details, parse_binding
from angee.workflows.data_contracts import DataContract, DataContractNode, model_data_contract, schema_data_contract
from angee.workflows.steps import StepEffect, StepImpl, StepOutcome, validate_retry_config

GraphLocationKind: TypeAlias = Literal["workflow", "node", "edge"]


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
    kind: GraphLocationKind
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
    join_rule: str = "all_success"


@dataclass(frozen=True, slots=True)
class GraphInputSource:
    kind: Literal["workflow_input", "step_output", "map_item"]
    contract: DataContract
    node_identity: GraphIdentity | None = None
    step_key: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class GraphMapBodyCandidate:
    """One exact prospective step and its Map-body eligibility."""

    identity: GraphIdentity
    key: str
    label: str
    eligible: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class GraphTestPlan:
    """One graph-owned node-test execution and fixture boundary."""

    selected: GraphIdentity
    executable: frozenset[GraphIdentity]
    output_sources: frozenset[GraphIdentity]
    map_item: bool


@dataclass(frozen=True, slots=True)
class GraphTestOperation:
    """One operation disclosed by an exact scoped test plan."""

    identity: GraphIdentity
    key: str
    label: str
    effect: StepEffect
    effect_description: str
    replaced_by_output: bool
    outcomes: tuple[StepOutcome, ...]


@dataclass(frozen=True, slots=True)
class GraphTestFixtureRequirement:
    """One fixture slot required to make a selected-node invocation executable."""

    role: Literal["output", "map_item"]
    identity: GraphIdentity
    step_key: str
    item_index_required: bool
    satisfied: bool


@dataclass(frozen=True, slots=True)
class GraphTestExecutionPlan:
    """Authoritative graph projection shared by test preview and admission."""

    selected: GraphIdentity | None
    executable: frozenset[GraphIdentity]
    output_sources: frozenset[GraphIdentity]
    map_item: bool
    operations: tuple[GraphTestOperation, ...]
    required_fixtures: tuple[GraphTestFixtureRequirement, ...]
    diagnostics: tuple[GraphDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class GraphFreshnessReason:
    """One semantic definition change affecting retained test evidence."""

    code: str
    step_key: str | None
    field: str


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
    subject_declaration: str = ""
    input_schema: Any = None
    output_schema: Any = None
    result_rules: Any = None

    @property
    def nodes_by_identity(self) -> dict[GraphIdentity, GraphNode]:
        """Return the exact node lookup for this immutable graph snapshot."""

        return {node.identity: node for node in self.nodes}

    @staticmethod
    def _node_output_contract(node: GraphNode) -> DataContract:
        if node.impl is None:
            return model_data_contract(None, mode="serialization")
        try:
            return node.impl.declared_output_contract(node.config)
        except ValidationError:
            return model_data_contract(None, mode="serialization")

    @staticmethod
    def _node_outcomes(node: GraphNode) -> tuple[StepOutcome, ...]:
        if node.impl is None:
            return ()
        try:
            return node.impl.declared_outcomes(node.config)
        except ValidationError:
            return ()

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
                    join_rule=str(getattr(step, "join_rule", "all_success")),
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
        return cls(
            workflow_identity,
            max_steps,
            tuple(nodes),
            graph_edges,
            str(getattr(workflow, "subject_declaration", "")),
            copy.deepcopy(getattr(workflow, "input_schema", None)),
            copy.deepcopy(getattr(workflow, "output_schema", None)),
            copy.deepcopy(getattr(workflow, "result_rules", None)),
        )

    def diagnostics(self) -> tuple[GraphDiagnostic, ...]:
        diagnostics = list(self.structural_diagnostics())
        diagnostics.extend(self._operations())
        map_targets, map_errors = self._maps()
        diagnostics.extend(map_errors)
        diagnostics.extend(self._routing(map_targets))
        diagnostics.extend(self._capacity(map_targets))
        diagnostics.extend(self._bindings(map_targets))
        diagnostics.extend(self._results(map_targets))
        return tuple(diagnostics)

    def input_sources(self, target_identity: GraphIdentity) -> tuple[GraphInputSource, ...]:
        """Return declared sources allowed by this exact prospective graph."""

        by_id = self.nodes_by_identity
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
                schema_data_contract(self.input_schema if isinstance(self.input_schema, dict) else {}),
                label="Workflow input",
            )
        ]
        sources.extend(
            GraphInputSource(
                "step_output",
                self._node_output_contract(source),
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

    def map_body_candidates(
        self,
        owner_identity: GraphIdentity,
    ) -> tuple[GraphMapBodyCandidate, ...]:
        """Project Map-body choices from the same graph facts as readiness."""

        by_id = self.nodes_by_identity
        owner = by_id.get(owner_identity)
        if owner is None or owner.impl is None or not owner.impl.map_body_operation:
            return ()
        targets, _ = self._maps()
        connected = {
            identity
            for edge in self.edges
            for identity in (edge.source_identity, edge.target_identity)
        }
        result: list[GraphMapBodyCandidate] = []
        for node in sorted(self.nodes, key=lambda candidate: (candidate.key, str(candidate.identity))):
            reason = None
            if node.identity == owner.identity:
                reason = "A Map cannot target itself."
            elif node.is_entry:
                reason = "The entry step cannot be a Map body."
            elif node.impl and node.impl.map_body_operation:
                reason = "A Map body cannot itself be a Map."
            elif node.identity in connected:
                reason = "A Map body cannot have ordinary connections."
            elif any(candidate.identity != owner.identity for candidate in targets.get(node.identity, ())):
                reason = "This step is already owned by another Map."
            result.append(
                GraphMapBodyCandidate(
                    node.identity,
                    node.key,
                    node.name or node.key,
                    reason is None,
                    reason,
                )
            )
        return tuple(result)

    def test_plan(self, selected_identity: GraphIdentity) -> GraphTestPlan:
        """Derive the exact executable node closure and fixture-source identities once."""

        selected = next((node for node in self.nodes if node.identity == selected_identity), None)
        if selected is None:
            return GraphTestPlan(selected_identity, frozenset(), frozenset(), False)
        executable = {selected_identity}
        declared_body = False
        for node in self.nodes:
            if node.impl is None:
                continue
            try:
                target = node.impl.map_body_target(node.config)
            except (ValidationError, ValueError, TypeError):
                continue
            if node.identity == selected_identity and target:
                body = next((candidate for candidate in self.nodes if candidate.key == target), None)
                if body is not None:
                    executable.add(body.identity)
            if target == selected.key:
                declared_body = True
        sources = self.input_sources(selected_identity)
        outputs = frozenset(
            source.node_identity for source in sources if source.node_identity is not None
        )
        return GraphTestPlan(
            selected_identity,
            frozenset(executable),
            outputs,
            declared_body or any(source.kind == "map_item" for source in sources),
        )

    def test_execution_plan(
        self,
        *,
        selected_identity: GraphIdentity | None,
        output_slots: frozenset[tuple[GraphIdentity, int | None]] = frozenset(),
        map_item_slots: frozenset[tuple[GraphIdentity, int]] = frozenset(),
    ) -> GraphTestExecutionPlan:
        """Return one immutable scoped plan for preview, validation and execution."""

        if selected_identity is None:
            executable = frozenset(node.identity for node in self.nodes)
            output_sources: frozenset[GraphIdentity] = frozenset()
            map_item = False
            diagnostics = self.test_diagnostics(output_slots=output_slots)
        else:
            node_plan = self.test_plan(selected_identity)
            executable = node_plan.executable
            output_sources = node_plan.output_sources
            map_item = node_plan.map_item
            structural = self.structural_diagnostics()
            relevant = tuple(
                item
                for item in self.test_diagnostics(
                    output_slots=output_slots,
                    selected_identity=selected_identity,
                )
                if item in structural
                or (
                    item.location.kind == "node"
                    and item.location.key in executable
                    and not (
                        item.code == "operation_not_executable"
                        and item.location.key == selected_identity
                        and map_item
                    )
                )
            )
            diagnostics = relevant
        requirements: list[GraphTestFixtureRequirement] = []
        if selected_identity is not None:
            selected = next((node for node in self.nodes if node.identity == selected_identity), None)
            references: tuple[Any, ...] = ()
            if selected is not None and selected.input_binding is not None:
                try:
                    binding = parse_binding(selected.input_binding)
                    references = tuple(
                        reference
                        for visit in binding.visits()
                        if (reference := visit.binding.source_reference()) is not None
                    )
                except PydanticValidationError:
                    references = ()
            by_key = {node.key: node for node in self.nodes}
            step_keys: set[str] = {
                reference.step_key
                for reference in references
                if reference.kind == "step_output" and reference.step_key is not None
            }
            for step_key in sorted(step_keys):
                source = by_key.get(step_key or "")
                if source is not None:
                    requirements.append(
                        GraphTestFixtureRequirement(
                            "output",
                            source.identity,
                            source.key,
                            False,
                            (source.identity, None) in output_slots,
                        )
                    )
            if map_item:
                indexes = sorted(index for identity, index in map_item_slots if identity == selected_identity)
                requirements.append(
                    GraphTestFixtureRequirement(
                        "map_item",
                        selected_identity,
                        selected.key if selected is not None else "",
                        True,
                        len(indexes) == 1,
                    )
                )
            diagnostics = (
                *diagnostics,
                *(
                    GraphDiagnostic(
                        "fixture_required",
                        (
                            "Provide the Current Map item."
                            if requirement.role == "map_item"
                            else f"Provide retained or manual output for {requirement.step_key!r}."
                        ),
                        GraphLocation(
                            "node",
                            selected_identity,
                            "fixtures",
                            (requirement.role, requirement.step_key),
                        ),
                    )
                    for requirement in requirements
                    if not requirement.satisfied
                ),
            )
        by_key = {node.key: node for node in self.nodes}
        omitted_operations: set[GraphIdentity] = set()
        for identity, index in output_slots:
            if index is not None:
                continue
            owner = next((node for node in self.nodes if node.identity == identity), None)
            target_key = owner.impl.map_body_target(owner.config) if owner and owner.impl else None
            target = by_key.get(target_key) if target_key else None
            if target is not None:
                omitted_operations.add(target.identity)
        operations = tuple(
            GraphTestOperation(
                identity=node.identity,
                key=node.key,
                label=node.name or node.key,
                effect=node.impl.effect if node.impl is not None else StepEffect.UNKNOWN,
                effect_description=node.impl.effect_description if node.impl is not None else "",
                replaced_by_output=(node.identity, None) in output_slots,
                outcomes=node.impl.outcomes if node.impl is not None else (),
            )
            for node in self.nodes
            if node.identity in executable and node.identity not in omitted_operations
        )
        return GraphTestExecutionPlan(
            selected_identity,
            executable,
            output_sources,
            map_item,
            operations,
            tuple(requirements),
            diagnostics,
        )

    def test_freshness(
        self,
        current: WorkflowGraph,
        *,
        selected_identity: GraphIdentity | None,
        plan: GraphTestExecutionPlan | None = None,
    ) -> tuple[GraphFreshnessReason, ...]:
        """Compare executable semantics while excluding presentation-only node fields."""

        before = {node.key: node for node in self.nodes}
        after = {node.key: node for node in current.nodes}
        plan = plan or self.test_execution_plan(selected_identity=selected_identity)
        relevant = {
            node.key
            for node in self.nodes
            if node.identity in plan.executable | plan.output_sources
        }
        selected = next((node for node in self.nodes if node.identity == selected_identity), None)
        if selected is not None:
            for node in self.nodes:
                target_key = node.impl.map_body_target(node.config) if node.impl else None
                if target_key == selected.key:
                    relevant.add(node.key)
        if selected_identity is None:
            relevant = set(before) | set(after)
        reasons: list[GraphFreshnessReason] = []
        for key in sorted(set(before) | set(after)):
            old = before.get(key)
            new = after.get(key)
            if key not in relevant:
                continue
            if old is None or new is None:
                reasons.append(GraphFreshnessReason("step_set_changed", key, "steps"))
                continue
            comparisons = (
                ("operation_changed", "step_class", old.operation_key, new.operation_key),
                ("config_changed", "config", old.config, new.config),
                ("input_binding_changed", "input_binding", old.input_binding, new.input_binding),
                ("entry_changed", "is_entry", old.is_entry, new.is_entry),
                ("join_rule_changed", "join_rule", old.join_rule, new.join_rule),
            )
            reasons.extend(
                GraphFreshnessReason(code, key, field)
                for code, field, old_value, new_value in comparisons
                if (
                    not json_values_equal(old_value, new_value)
                    if field in {"config", "input_binding"}
                    else old_value != new_value
                )
            )
        old_edges = {(edge.source_key, edge.target_key, edge.condition) for edge in self.edges}
        new_edges = {(edge.source_key, edge.target_key, edge.condition) for edge in current.edges}
        for source, target, _condition in sorted(old_edges ^ new_edges):
            if source in relevant or target in relevant:
                reasons.append(GraphFreshnessReason("routing_changed", target, "edges"))
        changed = {reason.step_key for reason in reasons if reason.step_key is not None}
        downstream: dict[str, set[str]] = defaultdict(set)
        for edge in (*self.edges, *current.edges):
            downstream[edge.source_key].add(edge.target_key)
        for graph in (self, current):
            for node in graph.nodes:
                target_key = node.impl.map_body_target(node.config) if node.impl else None
                if target_key:
                    downstream[node.key].add(target_key)
        affected = set(changed)
        pending = deque(changed)
        while pending:
            source = pending.popleft()
            for target in downstream.get(source, ()):
                if target not in affected:
                    affected.add(target)
                    pending.append(target)
        reasons.extend(
            GraphFreshnessReason("dependency_changed", key, "dependencies")
            for key in sorted((affected & relevant) - changed)
        )
        return tuple(reasons)

    def test_diagnostics(
        self,
        *,
        output_slots: frozenset[tuple[GraphIdentity, int | None]],
        selected_identity: GraphIdentity | None = None,
    ) -> tuple[GraphDiagnostic, ...]:
        """Return readiness after exact output substitutions remove execution work."""

        substituted = {identity for identity, index in output_slots if index is None}
        result: list[GraphDiagnostic] = []
        structural = self.structural_diagnostics()
        by_identity = self.nodes_by_identity
        by_key = {node.key: node for node in self.nodes}
        map_targets, _map_errors = self._maps()
        if selected_identity is not None and any(
            identity == selected_identity and index is not None
            for identity, index in output_slots
        ):
            substituted.add(selected_identity)
        for body_identity, owners in map_targets.items():
            if len(owners) != 1:
                continue
            items = owners[0].config.get("items") if isinstance(owners[0].config, dict) else None
            if isinstance(items, list) and all(
                (body_identity, index) in output_slots for index in range(len(items))
            ):
                substituted.add(body_identity)
        skipped_bodies: set[GraphIdentity] = set()
        for identity in substituted:
            owner = by_identity.get(identity)
            target_key = owner.impl.map_body_target(owner.config) if owner and owner.impl else None
            target = by_key.get(target_key) if target_key else None
            if target is not None:
                skipped_bodies.add(target.identity)
        for diagnostic in self.diagnostics():
            if diagnostic.location.kind == "node" and diagnostic.location.key in substituted | skipped_bodies:
                if diagnostic not in structural:
                    continue
            if diagnostic.code == "map_capacity":
                owner = by_identity.get(diagnostic.location.key)
                target_key = owner.impl.map_body_target(owner.config) if owner and owner.impl else None
                target = by_key.get(target_key) if target_key else None
                items = owner.config.get("items") if owner and isinstance(owner.config, dict) else None
                if target is not None and isinstance(items, list) and all(
                    (target.identity, index) in output_slots for index in range(len(items))
                ):
                    continue
            result.append(diagnostic)
        return tuple(result)

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
            required_subject = node.impl.subject_declaration
            if required_subject and required_subject != self.subject_declaration:
                result.append(
                    self._node(
                        node,
                        "subject_declaration_mismatch",
                        (
                            f"This operation requires workflow subject {required_subject!r}; "
                            "choose it in Works with."
                        ),
                        "step_class",
                    )
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
            declared = self._node_outcomes(source)
            if source.impl and edge.condition and declared:
                if edge.condition not in {outcome.key for outcome in declared}:
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
        by_key = {node.key: node for node in self.nodes}
        absent_reach: dict[str, set[GraphIdentity]] = {}
        outgoing: dict[GraphIdentity, list[GraphEdge]] = defaultdict(list)
        for edge in self.edges:
            outgoing[edge.source_identity].append(edge)
        for source in self.nodes:
            if source.operation_key != "call_workflow":
                continue
            pending = deque(
                edge.target_identity for edge in outgoing[source.identity]
                if edge.condition in {"", "child_failed", "child_canceled"}
            )
            reached: set[GraphIdentity] = set()
            while pending:
                identity = pending.popleft()
                if identity in reached:
                    continue
                reached.add(identity)
                pending.extend(edge.target_identity for edge in outgoing[identity])
            absent_reach[source.key] = reached
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
                    if (reference.step_key in absent_reach and node.identity in absent_reach[reference.step_key]
                            and by_key.get(reference.step_key) is not None):
                        result.append(self._binding(
                            node, "binding_source_absent_on_route",
                            "This call has no output on a child failure or cancellation route.",
                            visit.binding_path,
                        ))
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

    def _results(self, map_targets: dict[GraphIdentity, list[GraphNode]]) -> list[GraphDiagnostic]:
        """Validate explicit terminal producers and their one published output contract."""

        result: list[GraphDiagnostic] = []
        if not isinstance(self.input_schema, dict):
            result.append(self._workflow("input_schema_invalid", "Workflow input schema must be an object.", "input_schema"))
            return result
        try:
            Draft202012Validator.check_schema(self.input_schema)
        except Exception:  # noqa: BLE001 - preserve one authoring diagnostic for native schema errors.
            result.append(self._workflow("input_schema_invalid", "Workflow input schema is not valid JSON Schema.", "input_schema"))
            return result
        schema = self.output_schema
        rules = self.result_rules
        if not isinstance(schema, dict):
            return [self._workflow("output_schema_invalid", "Workflow output schema must be an object.", "output_schema")]
        try:
            Draft202012Validator.check_schema(schema)
        except Exception:  # noqa: BLE001 - preserve one authoring diagnostic for native schema errors.
            result.append(self._workflow("output_schema_invalid", "Workflow output schema is not valid JSON Schema.", "output_schema"))
            return result
        if not isinstance(rules, list):
            return [self._workflow("result_rules_invalid", "Terminal result rules must be a list.", "result_rules")]
        if not rules:
            if schema != {"type": "object", "properties": {}, "additionalProperties": False}:
                result.append(self._workflow("result_rules_missing", "A nonempty workflow output needs terminal result rules.", "result_rules"))
            return result
        by_key = {node.key: node for node in self.nodes}
        bodies = set(map_targets)
        ordinary = [node for node in self.nodes if node.identity not in bodies]
        outgoing: dict[GraphIdentity, list[GraphEdge]] = defaultdict(list)
        for edge in self.edges:
            outgoing[edge.source_identity].append(edge)
        eligible_exits = tuple(
            (node.key, outcome)
            for node in ordinary
            for outcome in (tuple(item.key for item in self._node_outcomes(node)) or ("",))
            if outcome not in {"child_failed", "child_canceled"}
            and not any(edge.condition in {"", outcome} for edge in outgoing[node.identity])
        )
        terminal_exits = set(eligible_exits)
        terminal = {node.key: node for node in ordinary if any(
            key == node.key for key, _ in terminal_exits
        )}
        workflow_input_contract = schema_data_contract(self.input_schema)
        seen_business: set[str] = set()
        seen_producers: set[tuple[str, str]] = set()
        covered: set[tuple[str, str]] = set()
        for index, raw in enumerate(rules):
            if not isinstance(raw, dict) or set(raw) != {"outcome", "producer", "when_outcome", "binding"}:
                result.append(self._workflow("result_rule_invalid", f"Result rule {index} needs outcome, producer, when_outcome and binding.", "result_rules"))
                continue
            business, producer_key, producer_outcome = (raw[key] for key in ("outcome", "producer", "when_outcome"))
            if (not isinstance(business, str) or not business or
                    not isinstance(producer_key, str) or not producer_key or
                    not isinstance(producer_outcome, str)):
                result.append(self._workflow("result_rule_invalid", f"Result rule {index} uses invalid keys.", "result_rules"))
                continue
            if business in seen_business or business in {"failed", "canceled"}:
                result.append(self._workflow("result_outcome_duplicate", f"Business result {business!r} is duplicated or reserved.", "result_rules"))
            seen_business.add(business)
            pair = (producer_key, producer_outcome)
            if pair in seen_producers:
                result.append(self._workflow("result_producer_duplicate", f"Producer outcome {pair!r} is duplicated.", "result_rules"))
            seen_producers.add(pair)
            producer = by_key.get(producer_key)
            if producer is None or terminal.get(producer_key) is not producer or pair not in terminal_exits:
                result.append(self._workflow("result_producer_not_terminal", f"Producer {producer_key!r}/{producer_outcome!r} must be one ordinary terminal exit.", "result_rules"))
                continue
            declared = {outcome.key for outcome in self._node_outcomes(producer)} or {""}
            if producer_outcome not in declared or producer_outcome in {"child_failed", "child_canceled"}:
                result.append(self._workflow("result_producer_outcome", f"Producer {producer_key!r} does not declare successful outcome {producer_outcome!r}.", "result_rules"))
                continue
            covered.add(pair)
            try:
                binding = parse_binding(raw["binding"])
            except PydanticValidationError:
                result.append(self._workflow("result_binding_invalid", f"Result rule {index} has an invalid binding.", "result_rules"))
                continue
            producer_contract = self._node_output_contract(producer)
            for visit in binding.visits():
                reference = visit.binding.source_reference()
                if reference is not None:
                    if reference.kind == "map_item" or (reference.kind == "step_output" and reference.step_key != producer_key):
                        result.append(self._workflow("result_binding_source", f"Result rule {index} may read only workflow input and its producer.", "result_rules"))
                    elif reference.kind == "workflow_input" and not workflow_input_contract.matches_path(reference.path):
                        result.append(self._workflow("result_binding_path", f"Result rule {index} reads outside workflow input.", "result_rules"))
                    elif reference.kind == "workflow_input" and (
                        Draft202012Validator(self.input_schema).is_valid(None)
                        or not workflow_input_contract.guarantees_path(reference.path)
                    ):
                        result.append(self._workflow(
                            "result_binding_optional_source",
                            f"Result rule {index} cannot guarantee workflow input is present at this path.",
                            "result_rules",
                        ))
                    elif reference.kind == "step_output" and not producer_contract.matches_path(reference.path):
                        result.append(self._workflow("result_binding_path", f"Result rule {index} reads outside producer output.", "result_rules"))
                    elif reference.kind == "step_output" and not producer_contract.guarantees_path(reference.path):
                        result.append(self._workflow(
                            "result_binding_optional_source",
                            f"Result rule {index} cannot guarantee producer output is present at this path.",
                            "result_rules",
                        ))
            if not _result_binding_compatible(
                binding, schema, workflow_input_contract, producer_contract, producer_key
            ):
                result.append(self._workflow(
                    "result_binding_schema",
                    f"Result rule {index} cannot prove its binding satisfies the declared output schema.",
                    "result_rules",
                ))
        for node_key, outcome in eligible_exits:
            if (node_key, outcome) not in covered:
                result.append(self._workflow("result_exit_uncovered", f"Successful terminal exit {node_key!r}/{outcome!r} has no result rule.", "result_rules"))
        result.extend(self._result_mutual_exclusivity(terminal))
        return result

    def _result_mutual_exclusivity(self, terminal: dict[str, GraphNode]) -> list[GraphDiagnostic]:
        """Reject terminal producers that can run under compatible routing choices."""

        entries = [node for node in self.nodes if node.is_entry]
        if len(entries) != 1 or len(terminal) < 2:
            return []
        outgoing: dict[GraphIdentity, list[GraphEdge]] = defaultdict(list)
        for edge in self.edges:
            outgoing[edge.source_identity].append(edge)
        paths: dict[GraphIdentity, list[dict[GraphIdentity, str]]] = defaultdict(list)
        pending = [(entries[0].identity, {})]
        while pending:
            identity, choices = pending.pop()
            paths[identity].append(choices)
            if sum(map(len, paths.values())) > 10_000:
                return [self._workflow(
                    "result_exclusivity_unproven", "Terminal result routing is too large to prove unique.",
                    "result_rules",
                )]
            for edge in outgoing[identity]:
                next_choices = dict(choices)
                if edge.condition:
                    prior = next_choices.get(identity)
                    if prior is not None and prior != edge.condition:
                        continue
                    next_choices[identity] = edge.condition
                pending.append((edge.target_identity, next_choices))
        exits = [
            (terminal[raw["producer"]], raw["when_outcome"])
            for raw in self.result_rules
            if isinstance(raw, dict)
            and raw.get("producer") in terminal
            and isinstance(raw.get("when_outcome"), str)
        ]
        result: list[GraphDiagnostic] = []
        for index, (left, left_outcome) in enumerate(exits):
            for right, right_outcome in exits[index + 1:]:
                if left.identity == right.identity:
                    continue
                if any(
                    all(a.get(key, b.get(key)) == b.get(key, a.get(key)) for key in set(a) | set(b))
                    for original_left in paths[left.identity]
                    for original_right in paths[right.identity]
                    for a in ({**original_left, left.identity: left_outcome},)
                    for b in ({**original_right, right.identity: right_outcome},)
                ):
                    result.append(self._workflow(
                        "result_producers_coapplicable",
                        f"Terminal producers {left.key!r} and {right.key!r} can both complete.",
                        "result_rules",
                    ))
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


def _result_binding_compatible(
    binding: Any,
    target_schema: Any,
    workflow_input: DataContract,
    producer_output: DataContract,
    producer_key: str,
) -> bool:
    """Prove simple binding shapes against JSON Schema, rejecting unknown shapes.

    This uses the existing path catalogues for references and JSON Schema for
    constants. Complex source/target vocabulary remains a publication diagnostic;
    runtime validation still checks the exact resulting JSON value.
    """

    if not isinstance(target_schema, dict):
        return False
    kind = getattr(binding, "kind", None)
    if kind == "constant":
        try:
            return Draft202012Validator(target_schema).is_valid(binding.value)
        except Exception:  # noqa: BLE001 - unsupported local refs remain a publication diagnostic.
            return False
    variants = target_schema.get("oneOf", target_schema.get("anyOf"))
    if isinstance(variants, list):
        if set(target_schema) - {"oneOf", "anyOf", "$defs", "title", "description"}:
            return False
        if "oneOf" in target_schema:
            choice = _tagged_one_of_choice(binding, variants)
            return choice is not None and _result_binding_compatible(
                binding, choice, workflow_input, producer_output, producer_key,
            )
        return any(
            _result_binding_compatible(binding, choice, workflow_input, producer_output, producer_key)
            for choice in variants
        )
    if kind in {"workflow_input", "step_output"}:
        if kind == "step_output" and binding.step_key != producer_key:
            return False
        source = workflow_input if kind == "workflow_input" else producer_output
        source_node = source.catalogue.at_path(binding.path)
        return source_node is not None and _catalogue_node_compatible(source_node, target_schema)
    if kind == "object":
        if set(target_schema) - {
            "type", "properties", "required", "additionalProperties", "minProperties", "maxProperties",
            "$defs", "title", "description",
        }:
            return False
        if target_schema.get("type") not in (None, "object"):
            return False
        required = target_schema.get("required", ())
        if not isinstance(required, list | tuple) or not set(required).issubset(binding.fields):
            return False
        properties = target_schema.get("properties", {})
        if not isinstance(properties, dict):
            return False
        minimum, maximum = target_schema.get("minProperties", 0), target_schema.get("maxProperties")
        if type(minimum) is not int or len(binding.fields) < minimum:
            return False
        if maximum is not None and (type(maximum) is not int or len(binding.fields) > maximum):
            return False
        if target_schema.get("additionalProperties") is False and not set(binding.fields).issubset(properties):
            return False
        extra = target_schema.get("additionalProperties", {})
        if not isinstance(extra, dict | bool):
            return False
        return all(
            _result_binding_compatible(
                child, properties.get(key, extra if isinstance(extra, dict) else {}),
                workflow_input, producer_output, producer_key,
            )
            for key, child in binding.fields.items()
        )
    if kind == "array":
        if set(target_schema) - {"type", "items", "minItems", "maxItems", "$defs", "title", "description"}:
            return False
        if target_schema.get("type") not in (None, "array"):
            return False
        minimum, maximum = target_schema.get("minItems", 0), target_schema.get("maxItems")
        if not isinstance(minimum, int) or len(binding.items) < minimum:
            return False
        if maximum is not None and (not isinstance(maximum, int) or len(binding.items) > maximum):
            return False
        return all(
            _result_binding_compatible(child, target_schema.get("items", {}),
                                       workflow_input, producer_output, producer_key)
            for child in binding.items
        )
    return False


def _tagged_one_of_choice(binding: Any, variants: list[Any]) -> dict[str, Any] | None:
    """Select a disjoint object branch only when every variant requires one literal tag."""

    if getattr(binding, "kind", None) != "object" or not variants:
        return None
    tag_field: str | None = None
    tag_values: list[Any] = []
    for variant in variants:
        if not isinstance(variant, dict) or variant.get("type") != "object":
            return None
        required, properties = variant.get("required"), variant.get("properties")
        if not isinstance(required, list) or not isinstance(properties, dict):
            return None
        candidates = {
            key: properties[key]["const"]
            for key in required
            if key in properties and isinstance(properties[key], dict) and "const" in properties[key]
        }
        if tag_field is None:
            matches = [key for key in candidates if getattr(binding.fields.get(key), "kind", None) == "constant"]
            if len(matches) != 1:
                return None
            tag_field = matches[0]
        if tag_field not in candidates:
            return None
        value = candidates[tag_field]
        if any(value == prior for prior in tag_values):
            return None
        tag_values.append(value)
    assert tag_field is not None
    bound_value = binding.fields[tag_field].value
    matches = [variant for variant, value in zip(variants, tag_values, strict=True) if bound_value == value]
    return matches[0] if len(matches) == 1 else None


def _catalogue_node_compatible(source: DataContractNode, target_schema: dict[str, Any]) -> bool:
    if not target_schema:
        return True
    if set(target_schema) - {"type", "title", "description", "$defs", "items"}:
        return False
    target_type = target_schema.get("type")
    if source.kind == "unknown" or target_type is None:
        return False
    if source.nullable and target_type != "null" and (
        not isinstance(target_type, list) or "null" not in target_type
    ):
        return False
    allowed = set(target_type) if isinstance(target_type, list) else {target_type}
    if source.kind == "scalar":
        return source.json_type in allowed or (source.json_type == "integer" and "number" in allowed)
    if source.kind == "object":
        if "object" not in allowed:
            return False
        required = target_schema.get("required", ())
        if required:
            # The path catalogue deliberately does not project requiredness.
            # A whole-object reference cannot prove those fields are present.
            return False
        return True
    if source.kind == "array":
        if "array" not in allowed:
            return False
        item_schema = target_schema.get("items")
        if item_schema is None:
            return True
        return (
            isinstance(item_schema, dict)
            and source.item is not None
            and _catalogue_node_compatible(source.item.contract, item_schema)
        )
    return False


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
