"""Workflow-owned bounded path, schema composition and compatibility algebra."""
from __future__ import annotations
import copy
from collections.abc import Iterable, Iterator, Sequence
from typing import Any
from django.core.exceptions import ValidationError
from angee.base.schemas.references import LocalSchemaReferences, schema_nodes, schemas_match, SCHEMA_ANNOTATIONS, schema_intersection
from angee.base.validators import check_schema

def _prefix_references(schema: dict[str, Any], prefix: str) -> None:
    """Relocate proved root pointers at native schema locations in a copied schema."""
    for node in {id(node): node for node in schema_nodes(schema)}.values():
        if "$ref" in node:
            node["$ref"] = prefix + node["$ref"][1:]


def schema_at(
    schema: dict[str, Any], path: Sequence[str | int], *, required: bool = False,
) -> dict[str, Any] | None:
    """Follow an object/list path, optionally proving every part exists.

    Local references and allOf constraints contribute structural facts; dynamic
    references and unproven paths return None. This is not schema entailment.
    The returned contract retains the original root scope for references that
    remain inside the selected contract.
    """
    references = LocalSchemaReferences(schema)

    def follow(
        current: Any, remaining: Sequence[str | int], active: frozenset[str], prove_required: bool,
    ) -> dict[str, Any] | None:
        if not isinstance(current, dict) or "$dynamicRef" in current:
            return None
        if "$ref" in current:
            reference = current["$ref"]
            if reference in active:
                return None
            target = references.resolve(reference)
            if not isinstance(target, dict):
                return None
            siblings = {key: value for key, value in current.items() if key != "$ref" and key not in SCHEMA_ANNOTATIONS}
            current = schema_intersection([target, siblings]) if siblings else target
            return follow(current, remaining, active | {reference}, prove_required)
        if not remaining:
            return current
        part, *rest = remaining
        if isinstance(part, bool) or isinstance(part, int) and part < 0:
            return None
        guaranteed = part in current.get("required", []) if isinstance(part, str) else part < current.get("minItems", 0)
        if isinstance(part, str):
            child = current.get("properties", {}).get(part)
        else:
            positional = current.get("prefixItems", [])
            child = positional[part] if part < len(positional) else current.get("items")
        found = follow(child, rest, frozenset(), prove_required) if not prove_required or guaranteed else None
        contributions = [found] if found is not None else []
        for constraint in current.get("allOf", []):
            actual = follow(constraint, remaining, active, prove_required)
            if actual is not None:
                contributions.append(actual)
        return schema_intersection(contributions) if contributions else None

    if required and follow(schema, path, frozenset(), True) is None:
        return None
    actual = follow(schema, path, frozenset(), False)
    if actual is None:
        return None
    projected = copy.deepcopy(actual)
    nodes = list(schema_nodes(projected))
    if any("$dynamicRef" in node for node in nodes):
        return None
    if any("$ref" in node for node in nodes):
        definitions: dict[str, Any] = {}
        try:
            # Fragment-only references keep their scope when its root URI is removed.
            root = embed_schema({key: value for key, value in schema.items() if key != "$id"}, definitions)
        except ValidationError:
            return None
        _prefix_references(projected, root["$ref"])
        projected["$defs"] = definitions
    return projected


def unmatched_properties(source: dict[str, Any], target: dict[str, Any]) -> Iterator[str]:
    """Yield target fields whose declared contract cannot be supplied by source.

    Missing optional fields are permitted. Present fields require structural
    equality; this does not prove that source values contain required fields.
    Names follow target declaration order, retaining local reference scope.
    """
    target = schema_at(target, []) or {}
    for name in target.get("properties", {}):
        source_field = schema_at(source, [name])
        target_field = schema_at(target, [name])
        if source_field is None and name not in target.get("required", []):
            continue
        if source_field is None or target_field is None or not schemas_match(source_field, target_field):
            yield name


def embed_schema(
    schema: dict[str, Any], definitions: dict[str, Any], *, path: Sequence[str | int] = (), required: bool = False,
    into: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Embed a contract in definitions and constrain an object/array path to it.

    Definitions and an optional destination are mutated. The destination keeps
    its existing assertions, permits new top-level fields, and refuses additions
    beneath closed nested objects. Composition supports root-pointer references;
    anchors, explicit resource identities and dynamic scope are not relocated.
    References into literal data raise ValidationError: relocating that target
    could change its meaning as a literal. Other literal dictionaries are copied
    without interpreting their keys as schema.
    """
    check_schema(schema)
    embedded = copy.deepcopy(schema)
    name = f"schema_{len(definitions)}"
    while name in definitions:
        name += "_"
    prefix = f"#/$defs/{name}"
    nodes = list(schema_nodes(embedded))
    identities = {id(node) for node in nodes}
    references = LocalSchemaReferences(embedded)
    for node in nodes:
        if {"$id", "$dynamicRef", "$dynamicAnchor"} & node.keys():
            raise ValidationError("Composed schemas do not support resource identities or dynamic scope.")
        if "$ref" in node:
            reference = node["$ref"]
            if reference != "#" and not reference.startswith("#/"):
                raise ValidationError("Composed schemas require root-local JSON Pointer references.")
            target = references.resolve(reference)
            if isinstance(target, dict) and id(target) not in identities:
                raise ValidationError("Composed schemas cannot relocate references into literal data.")
    _prefix_references(embedded, prefix)
    constraint: dict[str, Any] = {"$ref": prefix}
    for part in reversed(path):
        if isinstance(part, str):
            constraint = {
                "type": "object", "properties": {part: constraint}, **({"required": [part]} if required else {}),
            }
        elif isinstance(part, int) and not isinstance(part, bool) and part >= 0:
            constraint = {
                "type": "array", "prefixItems": [{} for _ in range(part)] + [constraint],
                **({"minItems": part + 1} if required else {}),
            }
        else:
            raise ValidationError("Schema paths require string keys or nonnegative integer indexes.")
    if into is not None:
        for index in range(1, len(path)):
            parent = schema_at(into, path[:index])
            if (
                parent and parent.get("additionalProperties") is False
                and isinstance(path[index], str) and path[index] not in parent.get("properties", {})
            ):
                raise ValidationError("A schema cannot extend a closed nested field.")
        root = schema_at(into, []) or {}
        if root.get("type") == "object":
            expected = schema_at(schema, []) or {}
            additions = [path[0]] if path else expected.get("properties", {})
            for field in additions:
                if isinstance(field, str) and field not in root.get("properties", {}):
                    # allOf carries the actual constraint; this permits the field
                    # through the destination's additionalProperties boundary.
                    into.setdefault("properties", {})[field] = {}
        into.setdefault("allOf", []).append(constraint)
    definitions[name] = embedded
    return constraint


def union_schema(*schemas: dict[str, Any]) -> dict[str, Any]:
    """Combine alternatives, preserving local references and removing equal branches."""
    choices: list[dict[str, Any]] = []
    for schema in schemas:
        check_schema(schema)
        if not any(schemas_match(schema, existing) for existing in choices):
            choices.append(schema)
    if not choices:
        return {"not": {}}
    if len(choices) == 1:
        return copy.deepcopy(choices[0])
    definitions: dict[str, Any] = {}
    return {"anyOf": [embed_schema(schema, definitions) for schema in choices], "$defs": definitions}


def compose_schema(
    schema: dict[str, Any],
    constraints: Iterable[tuple[Sequence[str | int], dict[str, Any], bool, bool]],
) -> dict[str, Any]:
    """Constrain paths to compatible contracts, preserving their local references.

    Each constraint supplies a path, schema, requiredness and projection flag.
    Projection permits additional object properties. Other repeated paths require
    structural equality; this deliberately does not attempt schema implication.
    """
    result = copy.deepcopy(schema)
    definitions = result.setdefault("$defs", {})
    seen: dict[tuple[str | int, ...], dict[str, Any]] = {}
    for path, expected, required, project in constraints:
        identity = tuple(path)
        known = seen.get(identity) or schema_at(result, path)
        if known and expected and not schemas_match(known, expected) and not project:
            raise ValidationError("Consumers declare incompatible schemas for the same path.")
        seen[identity] = expected or known or {}
        expected = copy.deepcopy(expected)
        if project:
            expected.pop("additionalProperties", None)
        embed_schema(expected, definitions, path=path, required=required, into=result)
    if not definitions:
        result.pop("$defs")
    return result
