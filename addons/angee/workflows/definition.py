"""The graph document, its validation, and database-free execution planning."""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Any, Literal, Protocol

from django.core.exceptions import ImproperlyConfigured, ValidationError
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from angee.base.fields.text import ModelLabelField
from angee.workflows.schemas import compose_schema, embed_schema, schema_at, union_schema, unmatched_properties
from angee.base.schemas.forms import relation_positions
from angee.base.schemas.references import schemas_match
from angee.base.validators import validate, validator
from angee.provenance.evidence import EvidenceReference
from angee.base.refs import generic_pointer_model
from angee.serialization import canonical_json, canonical_json_sha256
from angee.workflows.bindings import (
    ABSENT,
    InputBinding,
    SourceBinding,
    ValueBinding,
    resolve_bindings,
)
from angee.workflows.maps import Map
from angee.workflows.states import (
    DONE_OUTCOME,
    ERROR_OUTCOME,
    INPUT_SOURCE,
    ITEM_SOURCE,
    NodeKey,
    Outcome,
    StepRunStatus,
    WaitingKind,
)
from angee.workflows.steps import EmptyOutput, Step, resolve_step

MAP_BODY_SUFFIX = ".body"
type Position = tuple[
    Annotated[float, Field(strict=True, allow_inf_nan=False)],
    Annotated[float, Field(strict=True, allow_inf_nan=False)],
]
type Layout = dict[NodeKey, Position]
_LAYOUT: TypeAdapter[Layout] = TypeAdapter(Layout)
"""Persisted node positions share one finite, two-coordinate shape."""
_ACTION: TypeAdapter[NodeKey] = TypeAdapter(NodeKey)
"""Blocked actions use the same stable identifier vocabulary as graph declarations."""


def map_body_key(key: str) -> str:
    """Name the execution row for a map node's declared body."""
    return key + MAP_BODY_SUFFIX


class Issue(BaseModel):
    """One document issue located in the authored document."""

    node: str | None = None
    path: list[str | int] = Field(default_factory=list)
    code: str
    message: str

    @property
    def blocks_draft(self) -> bool:
        """Whether this issue rejects a draft as well as a publication."""
        return self.code in {"parse", "unknown_step"}


class DefinitionInvalid(ValidationError):
    """A document cannot be published; typed diagnostics remain available."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        fields: dict[str, list[str]] = {}
        for issue in issues:
            path = ".".join(str(part) for part in issue.path) if issue.path else "__all__"
            fields.setdefault(path, []).append(issue.message)
        super().__init__(fields)


class Body(BaseModel):
    """A nested step's input and configuration, without graph edges."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    step: str
    label: str = ""
    outcome_labels: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    input: InputBinding | None = None

    @cached_property
    def implementation(self) -> type[Step[Any, Any, Any]]:
        """Resolve this declaration's implementation once for all its contracts."""
        return resolve_step(self.step)

    @cached_property
    def parsed_config(self) -> Any:
        """Parse once for all declaration contracts, preserving validator semantics."""
        return self.implementation.parse_config(self.config)


class Node(Body):
    """One registered step and its data/control declarations."""

    body: Body | None = None
    join: Literal["any", "all"] = "any"
    next: dict[Outcome, str | list[str]] = Field(default_factory=dict)

    def targets(self, outcome: str) -> list[str]:
        """Return targets of one outcome, preserving authored fan-out order."""
        targets = self.next.get(outcome, [])
        return targets if isinstance(targets, list) else [targets]


class ExtensionPoint(BaseModel):
    """One named insertion boundary owned by a base workflow definition."""

    model_config = ConfigDict(extra="forbid")
    after: NodeKey | None = None
    before: NodeKey | None = None

    @model_validator(mode="after")
    def exactly_one_boundary(self) -> ExtensionPoint:
        """Require one unambiguous side of one base node."""

        if (self.after is None) == (self.before is None):
            raise ValueError("An extension point must declare exactly one of `after` or `before`.")
        return self


class ExtensionEdge(BaseModel):
    """One internal fragment route in the definition's native edge vocabulary."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)
    source: NodeKey = Field(alias="from")
    outcome: Outcome = DONE_OUTCOME
    target: NodeKey = Field(alias="to")


class WorkflowExtension(BaseModel):
    """A typed install declaration contributing one connected graph fragment."""

    model_config = ConfigDict(extra="forbid")
    extends: str = Field(min_length=1)
    at: NodeKey
    sequence: int = 0
    nodes: dict[NodeKey, Node] = Field(min_length=1)
    edges: list[ExtensionEdge] = Field(default_factory=list)
    bindings: dict[NodeKey, InputBinding] = Field(default_factory=dict)

    def fragment(self) -> dict[NodeKey, Node]:
        """Apply the optional flat edge/binding declarations to copied nodes."""

        nodes = {key: node.model_copy(deep=True) for key, node in self.nodes.items()}
        for key, binding in self.bindings.items():
            if key not in nodes:
                raise ValueError(f"Binding target {key!r} is not contributed by this extension.")
            if "input" in nodes[key].model_fields_set:
                raise ValueError(f"Node {key!r} declares input both under nodes and bindings.")
            nodes[key].input = copy.deepcopy(binding)
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes:
                raise ValueError(
                    f"Edge {edge.source!r}.{edge.outcome} -> {edge.target!r} must stay inside its extension."
                )
            targets = nodes[edge.source].targets(edge.outcome)
            if edge.target in targets:
                continue
            nodes[edge.source].next[edge.outcome] = [*targets, edge.target] if targets else edge.target
        return nodes


@dataclass(frozen=True)
class SourcedWorkflowExtension:
    """An extension declaration paired with its composer-ordered addon owner."""

    source_addon: str
    extension: WorkflowExtension
    declared_fingerprint: str | None = None

    def fingerprint(self) -> str:
        """Identify the exact source declaration across later retained publications."""

        return self.declared_fingerprint or canonical_json_sha256({
            "addon": self.source_addon,
            "definition": self.extension.model_dump(mode="json", by_alias=True),
        })


class PublishedContribution(BaseModel):
    """Provenance needed to display and retain a composed version's fragment."""

    model_config = ConfigDict(extra="forbid")
    addon: str
    extends: str
    at: NodeKey
    sequence: int
    nodes: tuple[NodeKey, ...]
    fingerprint: str


class ResultBinding(BaseModel):
    """A producer eligible to supply the terminal run result."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)
    source: str = Field(alias="from")
    when: list[Outcome] | None = None
    outcome: Outcome | None = Field(default=None, alias="as")
    output: InputBinding | None = None

    def eligible(self, outcome: str) -> bool:
        """Require an explicit selection to report a producer's failure."""
        return outcome != ERROR_OUTCOME if self.when is None else outcome in self.when


class StepRow(Protocol):
    """The minimal persisted facts consulted by graph planning."""

    node_key: str
    map_index: int
    status: str
    outcome: str
    input: Any
    waiting_kind: str


@dataclass(frozen=True)
class PlannedNode:
    """A node whose row should be inserted by the run owner."""

    node_key: str
    status: Literal["ready", "skipped"]
    rank: int
    map_index: int = 0
    existing: bool = False


@dataclass(frozen=True)
class GraphNode:
    """A published node's reader vocabulary, ports and execution order."""

    key: str
    label: str
    step_label: str
    rank: int
    outcomes: dict[str, str]
    body_key: str | None


@dataclass(frozen=True)
class GraphEdge:
    """One routed outcome from a published node to a target."""

    source: str
    outcome: str
    target: str


@dataclass(frozen=True)
class GraphTopology:
    """Payload-free structure of the immutable graph."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class Definition(BaseModel):
    """The sole declaration shape and graph policy for a workflow document."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    nodes: dict[NodeKey, Node] = Field(default_factory=dict)
    results: list[ResultBinding] = Field(default_factory=list)
    outcome_labels: dict[str, str] = Field(default_factory=dict)
    blocks: tuple[str, ...] = ()
    points: dict[NodeKey, ExtensionPoint] = Field(default_factory=dict)
    contributions: tuple[PublishedContribution, ...] = ()

    @field_validator("blocks")
    @classmethod
    def canonical_blocks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Validate and canonicalize ``app.Model:action`` declarations once."""

        normalized: list[str] = []
        for value in values:
            model_label, separator, action = value.partition(":")
            if not separator or ":" in action:
                raise ValueError("A workflow block must use the `app.Model:action` form.")
            try:
                model_label = generic_pointer_model(ModelLabelField.get_model(model_label))._meta.label
                action = _ACTION.validate_python(action, strict=True)
            except (ValueError, PydanticValidationError) as error:
                raise ValueError(f"Invalid workflow block {value!r}.") from error
            normalized.append(f"{model_label}:{action}")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Workflow blocks must be unique.")
        return tuple(normalized)

    @classmethod
    def rekey(
        cls,
        document: Any,
        *,
        keys: object = None,
        layout: Any = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Translate client identities through the typed routing and binding owners.

        Config and literal values stay opaque. Missing sources remain dangling;
        renamed map bodies retain their suffix. The result uses authored keys so
        subsequent diagnostics use the same captured identity map as the client.
        """
        if keys is not None:
            try:
                keys = TypeAdapter(dict[str, str]).validate_python(keys, strict=True)
            except PydanticValidationError as error:
                raise ValidationError(
                    {"node_keys": "Node keys must map client identities to authored keys."}
                ) from error
        addressed = copy.deepcopy(document)
        if keys is not None and isinstance(addressed, dict) and isinstance(addressed.get("nodes"), dict):
            nodes = addressed["nodes"]
            if set(nodes) != set(keys):
                raise DefinitionInvalid(
                    [Issue(path=["nodes"], code="parse", message="Node identities must match the key map.")]
                )
            duplicates = {key for key, count in Counter(keys.values()).items() if count > 1}
            if duplicates:
                raise DefinitionInvalid(
                    [
                        Issue(node=id_, path=["nodes", id_], code="parse", message="Node keys must be unique.")
                        for id_, key in keys.items() if key in duplicates
                    ]
                )
            ambiguous = [id_ for id_, key in keys.items() if key != id_ and key in keys]
            if ambiguous:
                raise DefinitionInvalid([
                    Issue(node=id_, path=["nodes", id_], code="parse",
                          message="A node key cannot equal another node's client identity.")
                    for id_ in ambiguous
                ])
            addressed["nodes"] = {keys[id_]: value for id_, value in nodes.items()}
        try:
            definition = cls.model_validate(addressed)
        except PydanticValidationError as error:
            raise DefinitionInvalid(cls.parse_issues(error)) from error
        keys = keys or {key: key for key in definition.nodes}

        def reference(source: str) -> str:
            if source in {INPUT_SOURCE, ITEM_SOURCE}:
                return source
            head, separator, suffix = source.partition(".")
            return keys.get(head, head) + separator + suffix

        def bindings(binding: InputBinding | None) -> None:
            for value in cls._bindings(binding).values():
                if isinstance(value, SourceBinding):
                    value.source = (
                        [reference(source) for source in value.source]
                        if isinstance(value.source, list)
                        else reference(value.source)
                    )

        for _, node, _ in definition.declarations():
            bindings(node.input)
        for node in definition.nodes.values():
            if "next" not in node.model_fields_set:
                continue
            node.next = {
                outcome: (
                    [reference(target) for target in targets] if isinstance(targets, list) else reference(targets)
                )
                for outcome, targets in node.next.items()
            }
        for point in definition.points.values():
            if point.after is not None:
                point.after = keys.get(point.after, point.after)
            if point.before is not None:
                point.before = keys.get(point.before, point.before)
        for contribution in definition.contributions:
            contribution.nodes = tuple(keys.get(key, key) for key in contribution.nodes)
        for result in definition.results:
            result.source = reference(result.source)
            bindings(result.output)
        positions = (
            cls.validate_layout({keys.get(key, key): value for key, value in layout.items()})
            if (isinstance(layout, dict))
            else cls.validate_layout(layout)
            if layout is not None
            else None
        )
        if positions is not None and (unknown := positions.keys() - definition.nodes.keys()):
            raise DefinitionInvalid([
                Issue(node=key, path=["layout", key], code="parse", message="Layout node is absent from the document.")
                for key in sorted(unknown)
            ])
        return definition.model_dump(mode="json", by_alias=True, exclude_unset=True), positions

    @staticmethod
    def validate_layout(layout: Any) -> dict[str, Any]:
        """Validate persisted layout through the single node-position declaration."""
        try:
            return _LAYOUT.dump_python(_LAYOUT.validate_python(layout), mode="json")
        except PydanticValidationError as error:
            raise ValidationError(
                {
                    ".".join(["layout", *(str(part) for part in item["loc"])]): item["msg"]
                    for item in error.errors(include_url=False, include_context=False, include_input=False)
                }
            ) from error

    def node_label(self, key: str) -> str:
        """Respect authored labels; unlabeled map groups use their key's title."""
        node = self.node(key)
        if node.label:
            return node.label
        map_group = isinstance(node, Node) and node.body is not None
        try:
            label = None if map_group else node.implementation.__dict__.get("label")
        except ImproperlyConfigured:
            label = None
        return label or key.replace("_", " ").capitalize()

    def node_step_label(self, key: str) -> str:
        """Name the implementation, retaining its declared key if it was removed."""
        node = self.node(key)
        try:
            return node.implementation.display_label()
        except ImproperlyConfigured:
            return node.step

    def topology(self) -> GraphTopology:
        """Project frozen labels and every routed port in stable execution order."""
        return GraphTopology(
            nodes=tuple(GraphNode(
                key=key, label=self.node_label(key), step_label=self.node_step_label(key), rank=rank,
                outcomes={outcome: self.node_outcome_label(key, outcome)
                          for outcome in sorted(node.outcome_labels.keys() | node.next.keys())},
                body_key=map_body_key(key) if node.body is not None else None,
            ) for key, rank in self.ranks.items() for node in (self.nodes[key],)),
            edges=tuple(GraphEdge(source, outcome, target)
                        for source in self.ranks for outcome in sorted(self.nodes[source].next)
                        for target in sorted(self.nodes[source].targets(outcome))),
        )

    def node_outcome_label(self, key: str, outcome: str) -> str:
        """Name a node result from its frozen declaration, including old documents."""
        node = self.node(key)
        if label := node.outcome_labels.get(outcome):
            return label
        try:
            label = node.implementation.available_outcomes(node.parsed_config).get(outcome)
        except ImproperlyConfigured:
            return outcome
        return label or outcome.replace("_", " ").capitalize()

    def run_outcome_label(self, outcome: str) -> str:
        """Name a terminal result using its published result vocabulary."""
        return self.outcome_labels.get(outcome) or outcome.replace("_", " ").capitalize()

    def published_document(self) -> dict[str, Any]:
        """Freeze reader labels with the validated graph and step outcome contracts."""
        document = self.model_dump(mode="json", by_alias=True)
        for key, node, _ in sorted(self.declarations(), key=lambda item: item[0]):
            stored = document["nodes"][key.partition(".")[0]]
            if key.endswith(MAP_BODY_SUFFIX):
                stored = stored["body"]
            stored["label"] = self.node_label(key)
            stored["outcome_labels"] = dict(sorted(
                node.implementation.available_outcomes(node.parsed_config).items()
            ))
        labels: dict[str, str] = {}
        for result in self.results:
            node = self.node(result.source)
            for outcome in sorted(node.implementation.available_outcomes(node.parsed_config)):
                if result.eligible(outcome):
                    label = (result.outcome.replace("_", " ").capitalize() if result.outcome
                             else self.node_outcome_label(result.source, outcome))
                    labels.setdefault(result.outcome or outcome, label)
        document["outcome_labels"] = dict(sorted(labels.items()))
        return document

    def with_extensions(self, sourced: Sequence[SourcedWorkflowExtension]) -> Definition:
        """Return a composed copy, ordered by source addon and then sequence."""

        addon_order: dict[str, int] = {}
        indexed = list(enumerate(sourced))
        for _index, contribution in indexed:
            addon_order.setdefault(contribution.source_addon, len(addon_order))
        ordered = [
            contribution
            for _index, contribution in sorted(
                indexed,
                key=lambda item: (
                    addon_order[item[1].source_addon],
                    item[1].extension.sequence,
                    item[0],
                ),
            )
        ]
        composed = self.model_copy(deep=True)
        seen = set(composed.nodes)
        prepared: list[tuple[SourcedWorkflowExtension, dict[NodeKey, Node], NodeKey, NodeKey]] = []
        issues: list[Issue] = []
        for contribution in ordered:
            extension = contribution.extension
            point = composed.points.get(extension.at)
            if point is None:
                issues.append(Issue(
                    path=["points", extension.at],
                    code="extension_point",
                    message=(
                        f"{contribution.source_addon} extends unknown point {extension.at!r}."
                    ),
                ))
                continue
            collisions = seen & extension.nodes.keys()
            issues.extend(
                Issue(
                    node=key,
                    path=["nodes", key],
                    code="extension_collision",
                    message=f"{contribution.source_addon} contributes duplicate node key {key!r}.",
                )
                for key in sorted(collisions)
            )
            if collisions:
                continue
            try:
                fragment = extension.fragment()
                entry, exit_ = self._fragment_boundary(fragment)
            except ValueError as error:
                issues.append(Issue(
                    path=["points", extension.at],
                    code="extension_fragment",
                    message=f"{contribution.source_addon}: {error}",
                ))
                continue
            seen.update(fragment)
            prepared.append((contribution, fragment, entry, exit_))
        if issues:
            raise DefinitionInvalid(issues)

        by_point: dict[str, list[tuple[SourcedWorkflowExtension, dict[NodeKey, Node], NodeKey, NodeKey]]] = {}
        for item in prepared:
            by_point.setdefault(item[0].extension.at, []).append(item)
        for point_name in sorted(by_point):
            point = composed.points[point_name]
            contributions = by_point[point_name]
            if point.after is not None:
                contributions = list(reversed(contributions))
            for _source, fragment, entry, exit_ in contributions:
                composed._insert_fragment(point, fragment, entry=entry, exit_=exit_)
        composed.contributions = tuple(
            PublishedContribution(
                addon=contribution.source_addon,
                extends=contribution.extension.extends,
                at=contribution.extension.at,
                sequence=contribution.extension.sequence,
                nodes=tuple(contribution.extension.nodes),
                fingerprint=contribution.fingerprint(),
            )
            for contribution, _fragment, _entry, _exit in prepared
        )
        return composed

    @staticmethod
    def _fragment_boundary(nodes: Mapping[NodeKey, Node]) -> tuple[NodeKey, NodeKey]:
        """Return the unique entry and exit of one internally connected fragment."""

        targets = {
            target
            for node in nodes.values()
            for outcome in node.next
            for target in node.targets(outcome)
        }
        unknown = targets - nodes.keys()
        if unknown:
            raise ValueError(
                "Fragment routes must stay inside contributed nodes; unknown target(s): "
                + ", ".join(sorted(unknown))
                + "."
            )
        entries = [key for key in nodes if key not in targets]
        exits = [key for key, node in nodes.items() if not any(node.targets(outcome) for outcome in node.next)]
        if len(entries) != 1 or len(exits) != 1:
            raise ValueError("A workflow extension requires exactly one entry and one exit node.")
        return entries[0], exits[0]

    def _insert_fragment(
        self,
        point: ExtensionPoint,
        fragment: Mapping[NodeKey, Node],
        *,
        entry: NodeKey,
        exit_: NodeKey,
    ) -> None:
        """Splice one prepared fragment at a declared base-node boundary."""

        self.nodes.update(fragment)
        if point.after is not None:
            anchor = self.nodes.get(point.after)
            if anchor is None:
                raise DefinitionInvalid([Issue(
                    node=point.after,
                    path=["points"],
                    code="extension_point",
                    message=f"Extension point references unknown node {point.after!r}.",
                )])
            routed = [(outcome, targets) for outcome in anchor.next if (targets := anchor.targets(outcome))]
            if len(routed) > 1:
                raise DefinitionInvalid([Issue(
                    node=point.after,
                    path=["nodes", point.after, "next"],
                    code="extension_point",
                    message="An `after` extension point requires at most one routed outcome.",
                )])
            if routed:
                outcome, targets = routed[0]
                original = anchor.next[outcome]
                anchor.next[outcome] = entry
                self.nodes[exit_].next[DONE_OUTCOME] = copy.deepcopy(original)
            else:
                anchor.next[DONE_OUTCOME] = entry
            return

        assert point.before is not None
        target = point.before
        if target not in self.nodes:
            raise DefinitionInvalid([Issue(
                node=target,
                path=["points"],
                code="extension_point",
                message=f"Extension point references unknown node {target!r}.",
            )])
        for node in self.nodes.values():
            if node is fragment.get(exit_):
                continue
            for outcome, declared in tuple(node.next.items()):
                targets = node.targets(outcome)
                if target not in targets:
                    continue
                replaced = [entry if candidate == target else candidate for candidate in targets]
                node.next[outcome] = replaced if isinstance(declared, list) else replaced[0]
        self.nodes[exit_].next[DONE_OUTCOME] = target

    def retained_extensions(self) -> tuple[SourcedWorkflowExtension, ...]:
        """Recover installed fragments from this immutable composed document."""

        retained: list[SourcedWorkflowExtension] = []
        for contribution in self.contributions:
            keys = set(contribution.nodes)
            nodes: dict[NodeKey, Node] = {}
            for key in contribution.nodes:
                node = self.nodes[key].model_copy(deep=True)
                next_: dict[Outcome, str | list[str]] = {}
                for outcome, declared in node.next.items():
                    targets = [target for target in node.targets(outcome) if target in keys]
                    if targets:
                        next_[outcome] = targets if isinstance(declared, list) else targets[0]
                node.next = next_
                nodes[key] = node
            retained.append(SourcedWorkflowExtension(
                contribution.addon,
                WorkflowExtension(
                    extends=contribution.extends,
                    at=contribution.at,
                    sequence=contribution.sequence,
                    nodes=nodes,
                ),
                contribution.fingerprint,
            ))
        return tuple(retained)

    @classmethod
    def check(cls, document: Any, *, subject_model: str | None = None) -> tuple[Definition | None, list[Issue]]:
        """Parse a document and return the complete typed validation issue list."""
        try:
            definition = cls.model_validate(document)
        except PydanticValidationError as error:
            return None, cls.parse_issues(error)
        return definition, definition.issues(subject_model=subject_model)

    @staticmethod
    def parse_issues(error: PydanticValidationError) -> list[Issue]:
        """Locate typed parse failures consistently for checks and authoring writes."""
        return [
            Issue(
                node=str(item["loc"][1]) if len(item["loc"]) > 1 and item["loc"][0] == "nodes" else None,
                path=list(item["loc"]),
                code="parse",
                message=item["msg"],
            )
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        ]

    @staticmethod
    def node_outcomes(
        key: str, node: Body, path: list[str | int], *, implementation: type[Step[Any, Any, Any]] | None = None,
    ) -> tuple[dict[str, str], list[Issue]]:
        """Offer one declaration's outcomes and locate its config or outcome refusal."""
        try:
            step = implementation or node.implementation
        except ImproperlyConfigured as error:
            return {}, [Issue(node=key, path=[*path, "step"], code="unknown_step", message=str(error))]
        fallback = step._with_error_outcome(step.outcomes)
        try:
            config = step.parse_config(node.config) if implementation is not None else node.parsed_config
        except ValidationError as error:
            if hasattr(error, "message_dict"):
                return fallback, [
                    Issue(node=key, path=[*path, *field.split(".")], code="config", message=message)
                    for field, messages in error.message_dict.items() for message in messages
                ]
            return fallback, [Issue(node=key, path=[*path, "config"], code="config", message=str(error))]
        try:
            return step.available_outcomes(config, validate=True), []
        except ValidationError as error:
            return fallback, [Issue(node=key, path=[*path, "step"], code="outcome", message=str(error))]

    def node(self, key: str) -> Body:
        """Return the declaration for one stable node key."""
        parent, separator, _ = key.partition(".")
        node = self.nodes[parent]
        if not separator:
            return node
        if key != map_body_key(parent) or node.body is None:
            raise KeyError(key)
        return node.body

    def declarations(self) -> Iterator[tuple[str, Body, list[str | int]]]:
        """Visit graph nodes and their nested bodies at authored document paths."""
        for key, node in self.nodes.items():
            yield key, node, ["nodes", key]
            if node.body is not None:
                yield map_body_key(key), node.body, ["nodes", key, "body"]

    def step(self, key: str) -> type[Step[Any, Any, Any]]:
        """Resolve one node's class once, without resolving unrelated draft nodes."""
        return self.node(key).implementation

    def output_schema(self, key: str, outcomes: set[str] | None = None) -> dict[str, Any]:
        """Resolve node output, including the map body's typed item contract."""
        step = self.step(key)
        return Map.output_schema_for(self.step(map_body_key(key))) if step is Map else step.output_schema(
            config=self.node(key).parsed_config, outcomes=outcomes,
        )

    def node_input_schema(self, key: str) -> dict[str, Any]:
        """Give map admission the authored list contract instead of list[Any]."""
        schema = self.step(key).input_schema()
        if self.step(key) is Map:
            schema = copy.deepcopy(schema)
            schema["properties"]["items"] = embed_schema(self.map_items_schema(key), schema.setdefault("$defs", {}))
        return schema

    def map_items_schema(self, key: str) -> dict[str, Any]:
        """Use the producer's array contract or infer external items from the body."""
        node = self.nodes[key]
        body = self.node(map_body_key(key))
        if self.step(map_body_key(key)) is Map:
            return {}
        binding = node.input if isinstance(node.input, SourceBinding) else (node.input or {}).get("items")
        if isinstance(binding, SourceBinding):
            for source in binding.sources:
                if source in self.ancestors(key):
                    path = [*binding.path, *(["items"] if binding is node.input else [])]
                    return schema_at(self.output_schema(source), path) or {}
        if node.input is None and self.predecessors[key]:
            source = next(iter(self.predecessors[key]))
            return schema_at(self.output_schema(source), ["items"]) or {}
        target = self.step(map_body_key(key)).input_schema()
        item = target if body.input is None else compose_schema({}, (
            (value.path, expected, field is None or field in target.get("required", []), value.project)
            for field, value in self._bindings(body.input).items()
            if isinstance(value, SourceBinding) and ITEM_SOURCE in value.sources
            and (expected := self._field_schema(target, field)) is not None
        ))
        definitions: dict[str, Any] = {}
        return {"type": "array", "items": embed_schema(item, definitions), "$defs": definitions}

    @cached_property
    def predecessors(self) -> dict[str, dict[str, set[str]]]:
        """Map each target to source nodes and the outcomes that connect them."""
        incoming: dict[str, dict[str, set[str]]] = {key: {} for key in self.nodes}
        for source, node in self.nodes.items():
            for outcome in node.next:
                for target in node.targets(outcome):
                    if target in incoming:
                        incoming[target].setdefault(source, set()).add(outcome)
        return incoming

    @cached_property
    def graph(self) -> dict[str, set[str]]:
        """Return the dependency graph shared by validation and execution."""
        return {key: set(self.predecessors[key]) for key in sorted(self.nodes)}

    @cached_property
    def ranks(self) -> dict[str, int]:
        """Stable execution ranks, with dependency order owned by graphlib."""
        graph = {key: sorted(sources) for key, sources in self.graph.items()}
        return {key: rank for rank, key in enumerate(TopologicalSorter(graph).static_order())}

    def plan(self, rows: Any, *, terminal: bool = False) -> dict[str, str]:
        """Linear timeline: executed, current, then post-dominators of all live paths.

        An outcome with no target is an end, including an explicit result on a
        branching node. Error routes are excluded from the unchosen future.
        """
        rows = {row.node_key: row for row in rows if not row.is_mapped}
        states = {}
        current = []
        for key in self.nodes:
            row = rows.get(key)
            if row is None or row.status == StepRunStatus.SKIPPED:
                states[key] = "not_run"
            elif row.status in {StepRunStatus.READY, StepRunStatus.RUNNING, StepRunStatus.WAITING} or (
                row.status == StepRunStatus.FAILED and self.unrouted_failure([row]) and not terminal
            ):
                states[key] = "current"
                current.append(key)
            else:
                states[key] = "done"
        if terminal:
            return states
        reachable = set()
        mandatory: dict[str, set[str]] = {}
        for key in sorted(self.nodes, key=self.ranks.__getitem__, reverse=True):
            node = self.nodes[key]
            try:
                outcome_keys = set(self.step(key).available_outcomes(node.parsed_config))
            except ImproperlyConfigured:
                # A removed implementation cannot prevent inspection of the frozen graph.
                outcome_keys = set(node.next) | {DONE_OUTCOME}
            paths = [set().union(*(mandatory[target] for target in node.targets(outcome)))
                     for outcome in outcome_keys if outcome != ERROR_OUTCOME]
            mandatory[key] = {key} | (set.intersection(*paths) if paths else set())
        frontier = current[:]
        while frontier:
            key = frontier.pop()
            for outcome in self.nodes[key].next:
                if outcome == ERROR_OUTCOME:
                    continue
                for target in self.nodes[key].targets(outcome):
                    if target not in reachable:
                        reachable.add(target)
                        frontier.append(target)
        certain = set().union(*(mandatory[key] for key in current))
        for key in reachable:
            if states[key] == "not_run":
                states[key] = "certain" if key in certain else "optional"
        return states

    @cached_property
    def entries(self) -> list[str]:
        """Return nodes with no predecessor, including incomplete drafts."""
        return [key for key, sources in self.graph.items() if not sources]

    @property
    def entry(self) -> str:
        """Derive the unique entry; incomplete drafts have no usable entry."""
        if len(self.entries) != 1:
            raise ValidationError("A workflow requires exactly one entry node.")
        return self.entries[0]

    def issues(self, *, subject_model: str | None = None) -> list[Issue]:
        """Return publication issues; only parse/unknown-step issues block drafts."""
        issues, offered = self._node_issues(subject_model)
        issues.extend(self._point_issues())
        issues.extend(self._graph_issues())
        issues.extend(self._input_issues())
        issues.extend(self._result_issues(offered))
        if not issues:
            issues.extend(self._derived_contract_issues())
        return issues

    def _point_issues(self) -> list[Issue]:
        """Require every named extension boundary to resolve inside the base graph."""

        return [
            Issue(
                node=target,
                path=["points", name, "after" if point.after is not None else "before"],
                code="extension_point",
                message=f"Extension point references unknown node {target!r}.",
            )
            for name, point in self.points.items()
            for target in (point.after or point.before,)
            if target not in self.nodes
        ]

    def _node_issues(self, subject_model: str | None) -> tuple[list[Issue], dict[str, dict[str, str]]]:
        """Check registered steps, their declarations, and authored outgoing edges."""
        issues: list[Issue] = []
        offered: dict[str, dict[str, str]] = {}
        for key, node, path in self.declarations():
            if key in {INPUT_SOURCE, ITEM_SOURCE}:
                issues.append(
                    Issue(
                        node=key,
                        path=path,
                        code="reserved_node",
                        message="This key is reserved for a binding source.",
                    )
                )
            outcomes, node_issues = self.node_outcomes(key, node, path)
            issues.extend(node_issues)
            if any(issue.code == "unknown_step" for issue in node_issues):
                continue
            step = self.step(key)
            if step is Map and (not isinstance(node, Node) or node.body is None):
                issues.append(Issue(node=key, path=path, code="body", message="A map requires one non-map body."))
            if isinstance(node, Node) and node.body is not None and step is not Map:
                issues.append(Issue(node=key, path=[*path, "body"], code="body", message="Only a map has a body."))
            required_subject = step.subject
            if (
                subject_model is not None
                and required_subject is not None
                and ModelLabelField.normalize(subject_model) != ModelLabelField.normalize(required_subject)
            ):
                issues.append(
                    Issue(
                        node=key,
                        path=[*path, "step"],
                        code="subject",
                        message=f"Step requires subject model {required_subject!r}.",
                    )
                )
            for name, schema in (("input", step.input_schema), ("output", step.output_schema)):
                try:
                    schema()
                except ValidationError as error:
                    issues.append(
                        Issue(node=key, path=[*path, "step"], code="schema", message=f"{name}: {error}")
                    )
            if node_issues:
                continue
            config = node.parsed_config
            if not isinstance(node, Node):
                continue
            offered[key] = outcomes
            for outcome in sorted(step.required_outcomes(config)):
                if not node.targets(outcome):
                    issues.append(Issue(
                        node=key, path=["nodes", key, "next", outcome], code="required_outcome",
                        message=f"This step requires a route for outcome {outcome!r}.",
                    ))
            for outcome in node.next:
                if not node.targets(outcome):
                    issues.append(
                        Issue(
                            node=key,
                            path=["nodes", key, "next", outcome],
                            code="target",
                            message="An outcome entry requires at least one target; omit it to end the branch.",
                        )
                    )
                if outcome not in outcomes:
                    issues.append(
                        Issue(
                            node=key,
                            path=["nodes", key, "next", outcome],
                            code="outcome",
                            message=f"Unknown outcome {outcome!r}.",
                        )
                    )
                for target in node.targets(outcome):
                    if target not in self.nodes:
                        issues.append(
                            Issue(
                                node=key,
                                path=["nodes", key, "next", outcome],
                                code="target",
                                message=f"Unknown target {target!r}.",
                            )
                        )
        return issues, offered

    def _graph_issues(self) -> list[Issue]:
        """Check acyclicity, the single entry, and reachability in stable order."""
        issues: list[Issue] = []
        try:
            _ = self.ranks
        except CycleError as error:
            issues.append(Issue(code="cycle", message=str(error)))
        if len(self.entries) != 1:
            issues.append(Issue(code="entry", message="A workflow requires exactly one entry node."))
        if len(self.entries) == 1:
            outgoing: dict[str, set[str]] = {key: set() for key in self.nodes}
            for target, sources in self.graph.items():
                for source in sources:
                    outgoing[source].add(target)
            reached = self._walk(outgoing, self.entries)
            issues.extend(
                Issue(node=key, path=["nodes", key], code="unreachable", message="Node is unreachable.")
                for key in self.nodes
                if key not in reached
            )
        return issues

    def _input_issues(self) -> list[Issue]:
        """Check explicit bindings and the default input supplied by incoming edges."""
        issues: list[Issue] = []
        incoming = self.predecessors
        for key, node, path in self.declarations():
            try:
                target_schema = self.node_input_schema(key)
            except (KeyError, ImproperlyConfigured):
                continue
            except ValidationError as error:
                if self.step(key) is Map:
                    issues.append(Issue(node=key, path=[*path, "input"], code="input", message=str(error)))
                continue
            if self.step(key) is Map and schema_at(self.map_items_schema(key), [0]) is None:
                issues.append(Issue(
                    node=key, path=[*path, "input"], code="input", message="Map items require a list schema.",
                ))
                continue
            parent = key.partition(".")[0]
            mapped = key != parent
            if node.input is not None:
                issues.extend(
                    self._binding_issues(
                        node.input,
                        target_schema,
                        key,
                        [*path, "input"],
                        allowed=self.ancestors(parent) | {INPUT_SOURCE} | ({ITEM_SOURCE} if mapped else set()),
                    )
                )
            elif mapped:
                actual = schema_at(self.map_items_schema(parent), [0])
                if target_schema and (actual is None or not schemas_match(actual, target_schema)):
                    issues.append(Issue(
                        node=key, path=[*path, "input"], code="input",
                        message="Incompatible default item input; bind explicitly.",
                    ))
            elif len(incoming[key]) > 1:
                issues.append(
                    Issue(
                        node=key,
                        path=["nodes", key, "input"],
                        code="input",
                        message="Multiple predecessor nodes require explicit input bindings.",
                    )
                )
            else:
                for source_key, routed_outcomes in incoming[key].items():
                    try:
                        source_step = self.step(source_key)
                        source_schema = self.output_schema(source_key, routed_outcomes - source_step.empty_outcomes)
                    except (ImproperlyConfigured, ValidationError):
                        continue
                    if (
                        routed_outcomes - source_step.empty_outcomes
                        and target_schema
                        and not schemas_match(source_schema, target_schema)
                    ):
                        issues.append(
                            Issue(
                                node=key,
                                path=["nodes", key, "input"],
                                code="input",
                                message=f"Incompatible default input from {source_key!r}; bind explicitly.",
                            )
                        )
                    if routed_outcomes & source_step.empty_outcomes and not self._validator(target_schema).is_valid({}):
                        issues.append(
                            Issue(
                                node=key,
                                path=["nodes", key, "input"],
                                code="input",
                                message=("An empty-output edge, including an error edge, supplies {}; "
                                         "required input fields need explicit bindings."),
                            )
                        )
        return issues

    def _result_issues(self, offered: Mapping[str, dict[str, str]]) -> list[Issue]:
        """Check producer outcomes and the permitted sources of terminal result bindings."""
        issues: list[Issue] = []
        for index, result in enumerate(self.results):
            path: list[str | int] = ["results", index]
            if result.source not in self.nodes:
                issues.append(Issue(path=path, code="result", message=f"Unknown result producer {result.source!r}."))
            elif result.source in offered:
                outcomes = offered[result.source]
                if result.when is not None and set(result.when) - outcomes.keys():
                    issues.append(Issue(path=path, code="result", message="Result names an unknown producer outcome."))
                if result.output is not None:
                    if isinstance(result.output, SourceBinding) and any(
                        result.eligible(outcome)
                        for outcome in self.step(result.source).empty_outcomes | {ERROR_OUTCOME}
                    ):
                        issues.append(
                            Issue(
                                path=[*path, "output"],
                                code="binding",
                                message=("A whole result binding cannot select empty output, "
                                         "including the error outcome."),
                            )
                        )
                    allowed = (
                        {result.source, INPUT_SOURCE}
                        if isinstance(result.output, SourceBinding)
                        else ({result.source} | self.ancestors(result.source))
                    )
                    issues.extend(
                        self._binding_issues(
                            result.output,
                            {},
                            None,
                            [*path, "output"],
                            allowed=allowed,
                            outcomes={result.source: set(result.when)} if result.when else {},
                        )
                    )
        return issues

    def _derived_contract_issues(self) -> list[Issue]:
        """Check composed admission schemas and required whole-result paths last."""
        issues: list[Issue] = []
        try:
            self.input_schema
            for index, result in enumerate(self.results):
                self.result_schema(result)
                if isinstance(result.output, SourceBinding):
                    for source in result.output.sources:
                        source_schema = self._source_schema(source, set(result.when) if result.when else None)
                        if schema_at(source_schema, result.output.path, required=True) is None:
                            issues.append(
                                Issue(
                                    path=["results", index, "output"],
                                    code="binding",
                                    message="A whole result binding path must be required at every level.",
                                )
                            )
        except ValidationError as error:
            issues.append(Issue(code="binding", message=str(error)))
        return issues

    def _binding_issues(
        self,
        binding: InputBinding,
        target: dict[str, Any],
        node: str | None,
        path: list[str | int],
        *,
        allowed: set[str],
        outcomes: Mapping[str, set[str]] | None = None,
    ) -> list[Issue]:
        issues: list[Issue] = []
        target = schema_at(target, []) or {}
        fields = self._bindings(binding)
        if not isinstance(binding, SourceBinding):
            issues.extend(Issue(
                node=node, path=[*path, required], code="binding", message="A required input field has no binding.",
            ) for required in sorted(set(target.get("required", [])) - binding.keys()))
        for field, value in fields.items():
            location = path if field is None else [*path, field]
            expected = self._field_schema(target, field)
            if expected is None:
                issues.append(Issue(node=node, path=location, code="binding", message="Unknown target input field."))
                continue
            if isinstance(value, ValueBinding):
                for error in self._validator(expected).iter_errors(value.value):
                    issues.append(Issue(
                        node=node, path=location, code="binding", message=f"{error.json_path}: {error.message}",
                    ))
                continue
            messages = [] if value.sources else ["A source list cannot be empty."]
            if value.project and (field is not None or not target):
                messages.append("Projection requires a whole-object input binding and target schema.")
            for source in value.sources:
                if source not in allowed:
                    messages.append("A binding source must be an allowed producer or control-flow ancestor.")
                    continue
                if source == INPUT_SOURCE:
                    continue
                try:
                    schema = (schema_at(self.map_items_schema(node.partition(".")[0]), [0]) or {}) if (
                        source == ITEM_SOURCE and node is not None
                    ) else self.output_schema(
                        source,
                        self.predecessors[node.partition(".")[0]].get(source) if node else (outcomes or {}).get(source),
                    )
                except (KeyError, ImproperlyConfigured):
                    messages.append(f"Unknown source {source!r}.")
                    continue
                except ValidationError:
                    # The source node already reports its invalid schema declaration.
                    continue
                actual = schema_at(schema, value.path)
                if actual is None:
                    messages.append("Unknown source output path.")
                elif expected and value.project:
                    messages.extend(
                        f"Projection cannot supply field {name!r}." for name in unmatched_properties(actual, expected)
                    )
                elif expected and not schemas_match(actual, expected):
                    messages.append("Source and target schemas differ; compatibility is unproven.")
            issues.extend(Issue(node=node, path=location, code="binding", message=message) for message in messages)
        return issues

    @cached_property
    def _validators(self) -> dict[str, Any]:
        return {}

    def _validator(self, schema: dict[str, Any]) -> Any:
        """Retain each checked schema validator for this immutable definition."""
        key = canonical_json(schema)
        if key not in self._validators:
            self._validators[key] = validator(schema)
        return self._validators[key]

    def _source_schema(self, source: str, outcomes: set[str] | None = None) -> dict[str, Any]:
        return self.input_schema if source == INPUT_SOURCE else self.output_schema(source, outcomes)

    @staticmethod
    def _field_schema(target: dict[str, Any], field: str | None) -> dict[str, Any] | None:
        """Whole and untyped bindings retain their contract; typed fields project it."""
        return target if field is None or not target else schema_at(target, [field])

    @staticmethod
    def _bindings(binding: InputBinding | None) -> Mapping[str | None, SourceBinding | ValueBinding]:
        if isinstance(binding, SourceBinding):
            return {None: binding}
        return {field: value for field, value in (binding or {}).items()}

    @staticmethod
    def _walk(graph: Mapping[str, set[str]], starts: Iterable[str]) -> set[str]:
        pending = list(starts)
        reached: set[str] = set()
        while pending:
            key = pending.pop()
            if key not in reached:
                reached.add(key)
                pending.extend(graph[key])
        return reached

    def ancestors(self, key: str) -> set[str]:
        """Return control ancestors through the shared cycle-tolerant graph walk."""
        return self._walk(self.graph, self.graph[key])

    def unrouted_failure(self, rows: Iterable[StepRow]) -> bool:
        """Whether a failed node has no declared route for its built-in failure."""
        return any(
            row.status == StepRunStatus.FAILED and self.retry_allowed(row.node_key)
            for row in rows
        )

    def retry_allowed(self, key: str) -> bool:
        """In-place recovery must not replay a failure already routed through error."""
        parent = key.partition(".")[0]
        return self.failure_port(key) not in self.nodes[parent].next

    @staticmethod
    def failure_port(key: str) -> str:
        """A map body reports failure through its parent's failed port."""
        return "failed" if "." in key else ERROR_OUTCOME

    def ready_nodes(self, rows: Iterable[StepRow], *, map_concurrency: int = 10) -> list[PlannedNode]:
        """Plan missing rows once all sources settle, propagating skips in one pass.

        Incoming edges are grouped by source: two alternative outcomes leading
        to the same target count as one predecessor for an ``all`` join.
        Failed ``error`` edges are live, matching the failure-routing contract.
        """
        if type(map_concurrency) is not int or map_concurrency < 1:
            raise ValueError("Map concurrency must be a positive integer.")
        rows = list(rows)
        existing = {row.node_key: row for row in rows if row.node_key in self.nodes}
        incoming = self.predecessors
        planned: list[PlannedNode] = []
        skipped: set[str] = set()
        for key, rank in self.ranks.items():
            if key in existing:
                parent = existing[key]
                if (self.nodes[key].body is not None and parent.status == StepRunStatus.WAITING
                        and parent.waiting_kind == WaitingKind.MAP):
                    bodies = {row.map_index: row for row in rows if row.node_key == map_body_key(key)}
                    total = len(parent.input["items"])
                    open_count = sum(row.status not in StepRunStatus.terminal_values() for row in bodies.values())
                    if len(bodies) == total and not open_count:
                        planned.append(PlannedNode(key, "ready", rank, existing=True))
                    else:
                        slots = max(0, map_concurrency - open_count)
                        for index in range(total):
                            if not slots:
                                break
                            if index not in bodies:
                                planned.append(PlannedNode(map_body_key(key), "ready", rank, map_index=index))
                                slots -= 1
                continue
            sources = incoming[key]
            if not sources:
                planned.append(PlannedNode(key, "ready", rank))
                continue
            if any(
                source not in skipped
                and (source not in existing or existing[source].status not in StepRunStatus.terminal_values())
                for source in sources
            ):
                continue
            live = [
                source not in skipped and self.edge_live(existing[source], outcomes)
                for source, outcomes in sources.items()
            ]
            active = all(live) if self.nodes[key].join == "all" else any(live)
            status: Literal["ready", "skipped"] = "ready" if active else "skipped"
            planned.append(PlannedNode(key, status, rank))
            if not active:
                skipped.add(key)
        return planned

    @staticmethod
    def edge_live(row: StepRow, outcomes: set[str]) -> bool:
        """Whether a retained settlement took one of the routed outcomes."""
        return StepRunStatus(row.status).has_outcome and row.outcome in outcomes

    def input_for(self, key: str, run_input: Any, rows: Iterable[Any], *, map_index: int = 0) -> Any:
        """Bind raw node input; the attempt persists it before typed validation."""
        rows = list(rows)
        node = self.node(key)
        step = self.step(key)
        outputs = self._outputs(rows)
        parent, separator, _ = key.partition(".")
        if separator:
            outputs[ITEM_SOURCE] = next(row for row in rows if row.node_key == parent).input["items"][map_index]
        if node.input is not None:
            value = resolve_bindings(
                node.input,
                run_input,
                outputs,
                target_schema=step.input_schema(),
            )
        elif separator:
            value = outputs[ITEM_SOURCE]
        elif key == self.entry:
            value = self._entry_input(run_input, step)
        else:
            source = next(iter(self.predecessors[key]))
            source_row = next(row for row in rows if row.node_key == source)
            value = source_row.output
        if value is ABSENT:
            raise ValidationError("The bound input source is absent.")
        if value is None:
            raise ValidationError("A whole step input cannot be null.")
        return value

    def _input_bindings(self) -> Iterator[tuple[SourceBinding, dict[str, Any], bool]]:
        for key, node, _ in self.declarations():
            for field, binding in self._bindings(node.input).items():
                if isinstance(binding, SourceBinding) and INPUT_SOURCE in binding.sources:
                    target = schema_at(self.node_input_schema(key), []) or {}
                    expected = self._field_schema(target, field)
                    if expected is None:
                        raise ValidationError("Unknown target input field.")
                    yield binding, expected, field is None or field in target.get("required", [])

    @cached_property
    def input_schema(self) -> dict[str, Any]:
        """Derive the admitted input from the entry and explicit input consumers.

        Binding paths contribute object properties or positional array items.
        Multiple consumers of the same path must declare equal schemas; broader
        schema implication is deliberately outside the binding contract.
        """
        entry = self.nodes[self.entry]
        schema = {"type": "object"}
        if entry.input is None:
            schema = schema_at(self.node_input_schema(self.entry), []) or schema
        return compose_schema(schema, (
            (binding.path, expected, required, binding.project)
            for binding, expected, required in self._input_bindings()
        ))

    def validate_input(self, value: Any) -> Any:
        """Normalize the entry and validate the derived workflow-input schema."""
        step = self.step(self.entry)
        normalized = step.normalize_input(self.input_for(self.entry, value, []))
        if self.nodes[self.entry].input is None:
            value = {**value, **normalized} if isinstance(value, dict) and isinstance(normalized, dict) else normalized
        validate(self._validator(self.input_schema), value)
        return value

    def config_references(self) -> Iterator[tuple[str, tuple[str | int, ...], dict[str, Any], str]]:
        """Locate only schema-declared configuration relations, including map bodies."""
        for key, node, path in self.declarations():
            try:
                step = self.step(key)
            except ImproperlyConfigured:
                continue  # Definition.issues owns unknown-step diagnostics.
            if step.config_model is not None:
                for nested, relation, item in relation_positions(
                    step.config_model.model_json_schema(by_alias=True), node.config,
                ):
                    yield key, (*path, "config", *nested), relation, item

    def _input_references(self, value: Any) -> tuple[tuple[tuple[str | int, ...], EvidenceReference], ...]:
        """Locate the declared record references in the admitted run input."""
        return tuple(
            (path, EvidenceReference(model=relation["resource"], id=item))
            for path, relation, item in relation_positions(self.input_schema, value)
        )

    def input_evidence(self, value: Any) -> tuple[EvidenceReference, ...]:
        """Project the declared source identities needed at admission."""
        references = {(ref.model, ref.id) for _, ref in self._input_references(value)}
        return tuple(EvidenceReference(model=model, id=public_id) for model, public_id in sorted(references))

    def redacted_input(self, value: Any, readable: set[tuple[str, str]]) -> Any:
        """Null only reference-marked values whose current source read is absent."""
        hidden = [path for path, ref in self._input_references(value) if (ref.model.lower(), ref.id) not in readable]
        if not hidden:
            return value
        projected = copy.deepcopy(value)
        for path in hidden:
            if not path:
                return None
            parent = projected
            for part in path[:-1]:
                parent = parent[part]
            parent[path[-1]] = None
        return projected

    def _entry_input(self, value: Any, step: type[Step[Any, Any, Any]]) -> Any:
        if not isinstance(value, dict) or step.input_model is None:
            return value
        properties = (schema_at(step.input_schema(), []) or {}).get("properties", {})
        additional: set[str | int] = set()
        for binding, expected, _required in self._input_bindings():
            additional.update(binding.path[:1] or (schema_at(expected, []) or {}).get("properties", {}))
        return {key: item for key, item in value.items() if key not in additional or key in properties}

    def result_schema(self, result: ResultBinding) -> dict[str, Any]:
        """Describe one result's actual projection, retaining local schema refs."""
        if result.output is None:
            step = self.step(result.source)
            outcomes = set(result.when) if result.when else None
            if any(result.eligible(outcome) for outcome in step.empty_outcomes):
                empty = Step._schema(EmptyOutput, "serialization")
                return union_schema(self.output_schema(result.source, outcomes), empty) if (
                    result.when is None or set(result.when) - step.empty_outcomes
                ) else empty
            return self.output_schema(result.source, outcomes)
        definitions: dict[str, Any] = {}
        properties: dict[str, Any] = {}
        required: list[str] = []
        for field, binding in self._bindings(result.output).items():
            if isinstance(binding, ValueBinding):
                actual: dict[str, Any] = {"const": binding.value}
                if field is not None:
                    required.append(field)
            else:
                choices = []
                for source in binding.sources:
                    source_schema = self._source_schema(
                        source, set(result.when) if source == result.source and result.when else None,
                    )
                    source_value = schema_at(source_schema, binding.path)
                    if source_value is None:
                        raise ValidationError("Unknown result output path.")
                    choices.append(embed_schema(source_value, definitions))
                actual = choices[0] if len(choices) == 1 else {"anyOf": choices}
            if field is None:
                return {**actual, "$defs": definitions}
            properties[field] = actual
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
            "$defs": definitions,
        }

    @cached_property
    def output_schemas(self) -> dict[str, dict[str, Any]]:
        """Combine result contracts with native terminal fallbacks by outcome."""
        empty = Step._schema(EmptyOutput, "serialization")
        alternatives = {outcome: [empty] for outcome in ("done", ERROR_OUTCOME, "canceled")}
        for result in self.results:
            step = self.step(result.source)
            for outcome in step.available_outcomes(self.node(result.source).parsed_config):
                if result.eligible(outcome):
                    schema = self.result_schema(result.model_copy(update={"when": [outcome]}))
                    alternatives.setdefault(result.outcome or outcome, []).append(schema)
        return {outcome: union_schema(*choices) for outcome, choices in alternatives.items()}

    def result_for(self, rows: Iterable[Any], run_input: Any) -> tuple[str, Any] | None:
        """Select the first eligible result in authored document order."""
        existing = {row.node_key: row for row in rows}
        outputs = self._outputs(existing.values())
        for result in self.results:
            producer = existing.get(result.source)
            if producer is None or not StepRunStatus(producer.status).has_outcome:
                continue
            outcome = producer.outcome
            if not result.eligible(outcome):
                continue
            output = producer.output
            if result.output is not None:
                output = resolve_bindings(result.output, run_input, outputs)
                if output is ABSENT:
                    raise ValidationError("The bound result source is absent.")
            return result.outcome or outcome, output
        return None

    @staticmethod
    def _outputs(rows: Iterable[Any]) -> dict[str, Any]:
        return {row.node_key: row.output for row in rows if row.status == StepRunStatus.SUCCEEDED}
