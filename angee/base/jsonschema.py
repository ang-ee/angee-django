"""Draft 2020-12 validation and bounded structural schema composition."""

import copy
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

from django.core.exceptions import ValidationError
from jsonschema import Draft202012Validator, FormatChecker, SchemaError, validators
from jsonschema._utils import find_additional_properties
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012, UnknownDialect

from angee.base.serialization import canonical_json

_ANNOTATIONS = frozenset({"title", "description", "default", "examples"})


class LocalSchemaReferences:
    """Resolve root pointers and anchors; remote and nested resource scopes are unsupported."""

    def __init__(self, root: Any) -> None:
        self.root = root
        self.resolver = Registry().resolver_with_root(DRAFT202012.create_resource(root))

    def resolve(self, reference: Any) -> Any:
        """Return a root-local target, or None for unresolved/unsupported pointers.

        Native pointer traversal can raise ValueError for a nonnumeric array
        index and TypeError for traversal into a scalar, besides Unresolvable.
        These are malformed declarations, not value-validation failures.
        """
        if isinstance(reference, str) and reference.startswith("#"):
            try:
                resolved = self.resolver.lookup(reference)
                if resolved.resolver.lookup("#").contents is self.root:
                    return resolved.contents
            except (Unresolvable, ValueError, TypeError):
                pass
        return None


def schema_nodes(schema: Any) -> Iterator[dict[str, Any]]:
    """Visit native schema locations, leaving literal and extension data intact."""
    if isinstance(schema, dict):
        yield schema
        children = DRAFT202012.create_resource(schema).subresources()
        for resource in sorted(children, key=lambda child: canonical_json(child.contents)):
            yield from schema_nodes(resource.contents)


def _prefix_references(schema: dict[str, Any], prefix: str) -> None:
    """Relocate proved root pointers at native schema locations in a copied schema."""
    for node in {id(node): node for node in schema_nodes(schema)}.values():
        if "$ref" in node:
            node["$ref"] = prefix + node["$ref"][1:]


def check_schema(schema: dict[str, Any] | bool) -> None:
    """Check a declaration and every local reference, including unused definitions.

    Root pointers and anchors are supported. Remote references, nested resource
    scopes and other schema dialects are rejected before validating any value.
    Native dynamic references are checked too; structural helpers remain more
    conservative than the validator.
    """
    try:
        Draft202012Validator.check_schema(schema)
        references = LocalSchemaReferences(schema)
        pending = list(schema_nodes(schema))
        visited: set[int] = set()
        for node in pending:
            if id(node) in visited:
                continue
            visited.add(id(node))
            if "$schema" in node and node["$schema"].rstrip("#") != Draft202012Validator.META_SCHEMA["$id"]:
                raise ValidationError("JSON Schema declarations require Draft 2020-12.")
            if node is not schema and "$id" in node:
                raise ValidationError("JSON Schema declarations do not support nested resource scopes.")
            for keyword in ("$ref", "$dynamicRef"):
                if keyword in node:
                    target = references.resolve(node[keyword])
                    if target is None:
                        raise ValidationError(f"Unresolved or non-local JSON Schema reference {node[keyword]!r}.")
                    Draft202012Validator.check_schema(target)
                    pending.extend(schema_nodes(target))
    except SchemaError as error:
        raise ValidationError(f"Invalid JSON Schema: {error.message}") from error
    except UnknownDialect as error:
        raise ValidationError(f"Unsupported JSON Schema dialect {error.uri!r}.") from error


def validator(schema: dict[str, Any] | bool) -> Draft202012Validator:
    """Construct a checked, format-asserting native validator without retrieval."""
    check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker(), registry=Registry())


def validate(schema: dict[str, Any] | bool | Draft202012Validator, value: Any) -> None:
    """Raise one Django validation error containing the native ordered messages."""
    checked = schema if isinstance(schema, Draft202012Validator) else validator(schema)
    errors = [f"{error.json_path}: {error.message}" for error in checked.iter_errors(value)]
    if errors:
        raise ValidationError(errors)


def validation_issues(
    schema: dict[str, Any], value: Any, *, defaults: bool = False,
    keywords: Mapping[str, Callable[..., Any]] | None = None,
) -> dict[str, list[str]]:
    """Return Django field errors through native keyword hooks and format checks.

    With defaults enabled, missing object properties receive copied defaults
    before assertions at every node, independent of serialized keyword order.
    The supplied value is mutated; callers retaining raw input must copy it.
    """
    try:
        canonical_json(value)
    except (ValueError, TypeError):
        return {"__all__": ["Values must contain finite JSON values."]}
    checked = validator(schema)

    def properties(native: Any, fields: dict[str, Any], instance: Any, parent: Any) -> Any:
        if defaults and isinstance(instance, dict):
            for name, field in fields.items():
                if name not in instance and isinstance(field, dict) and "default" in field:
                    instance[name] = copy.deepcopy(field["default"])
        yield from Draft202012Validator.VALIDATORS["properties"](native, fields, instance, parent)

    native_class = validators.create(
        meta_schema=checked.META_SCHEMA,
        validators={**checked.VALIDATORS, **(keywords or {}), "properties": properties},
        type_checker=checked.TYPE_CHECKER, format_checker=checked.FORMAT_CHECKER, id_of=checked.ID_OF,
        applicable_validators=lambda node: sorted(node.items(), key=lambda item: (item[0] != "properties", item[0])),
    )
    issues: dict[str, list[str]] = {}
    for error in native_class(schema, format_checker=checked.format_checker, registry=Registry()).iter_errors(value):
        path = list(error.absolute_path)
        fields = [path]
        if error.validator == "required":
            fields = [path + [name] for name in error.validator_value if name not in error.instance]
        elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
            fields = [path + [name] for name in find_additional_properties(error.instance, error.schema)]
        for parts in fields:
            issues.setdefault(".".join(map(str, parts)) or "__all__", []).append(error.message)
    return issues


def materialize_schema(
    schema: dict[str, Any], *, annotations: frozenset[str] = _ANNOTATIONS,
) -> dict[str, Any]:
    """Copy a finite schema with local references inlined and unused definitions removed.

    Assertions beside a reference remain constraints; local annotations take
    precedence for presentation. Recursive references cannot be materialized.
    Literal JSON values and extension metadata remain untouched.
    """
    check_schema(schema)
    if any("$dynamicRef" in node or "$dynamicAnchor" in node for node in schema_nodes(schema)):
        raise ValidationError("Materialized schemas do not support dynamic scope.")
    references = LocalSchemaReferences(schema)

    def expand(current: Any, active: frozenset[str]) -> Any:
        if not isinstance(current, dict):
            return current
        result = copy.deepcopy(current)
        if "$ref" in result:
            reference = result.pop("$ref")
            if reference in active:
                raise ValidationError("Materialized schemas require finite local references.")
            target = expand(references.resolve(reference), active | {reference})
            if target is False:
                return False
            metadata = {key: result.pop(key) for key in list(result) if key in annotations}
            result = _intersection([target, result]) if isinstance(target, dict) else result
            result.update(metadata)
        result.pop("$defs", None)
        for child in DRAFT202012.create_resource(result).subresources():
            if isinstance(child.contents, dict):
                expanded = expand(child.contents, active)
                child.contents.clear()
                child.contents.update(expanded if isinstance(expanded, dict) else {"not": {}})
        return result

    materialized = expand(schema, frozenset())
    return materialized if isinstance(materialized, dict) else {"not": {}}


def _intersection(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine compatible keys, retaining conflicting assertions through allOf."""
    combined: dict[str, Any] = {}
    for part in parts:
        for key, value in part.items():
            if key in combined and key in _ANNOTATIONS:
                continue
            if key in combined and canonical_json(combined[key]) != canonical_json(value):
                if key == "properties" and schemas_match(
                    {"properties": combined[key]}, {"properties": value},
                ):
                    continue
                return {"allOf": parts}
            combined[key] = value
    return combined


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
            siblings = {key: value for key, value in current.items() if key != "$ref" and key not in _ANNOTATIONS}
            current = _intersection([target, siblings]) if siblings else target
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
        return _intersection(contributions) if contributions else None

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


def schemas_match(source: dict[str, Any], target: dict[str, Any]) -> bool:
    """Prove structural equality, ignoring presentation/default annotations.

    Native schema locations distinguish annotations from literal object keys.
    Assertion siblings of references remain constraints. Dynamic scope and
    references into literal data are not proven by this bounded algebra, even
    when two declarations look alike.
    """
    def normalize(root: dict[str, Any]) -> Any:
        nodes = list(schema_nodes(root))
        if any("$dynamicRef" in node or "$dynamicAnchor" in node for node in nodes):
            return None
        identities = {id(node) for node in nodes}
        references = LocalSchemaReferences(root)
        if any(
            "$ref" in node and isinstance(resolved := references.resolve(node["$ref"]), dict)
            and id(resolved) not in identities
            for node in nodes
        ):
            return None

        def visit(value: Any, active: frozenset[str]) -> Any:
            if isinstance(value, list):
                return [visit(item, active) for item in value]
            if not isinstance(value, dict):
                return value
            is_schema = id(value) in identities
            result = {
                key: visit(item, active)
                for key, item in value.items()
                if not is_schema or key not in _ANNOTATIONS | {"$defs", "$ref"}
            }
            if is_schema and "$ref" in value:
                reference = value["$ref"]
                resolved = references.resolve(reference)
                if resolved is None:
                    raise ValidationError(f"Unresolved or non-local JSON Schema reference {reference!r}.")
                target = {"$ref": reference} if reference in active else visit(resolved, active | {reference})
                return {"allOf": [target, result]} if result else target
            return result

        return visit(root, frozenset())

    left, right = normalize(source), normalize(target)
    return left is not None and right is not None and canonical_json(left) == canonical_json(right)


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
