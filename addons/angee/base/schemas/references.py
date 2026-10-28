"""Root-local native JSON Schema references and bounded structural equality."""
from __future__ import annotations
from collections.abc import Iterator
from typing import Any
from django.core.exceptions import ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012
from angee.serialization import canonical_json

SCHEMA_ANNOTATIONS = frozenset({"title", "description", "default", "examples"})

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


def schema_intersection(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine compatible keys, retaining conflicting assertions through allOf."""
    combined: dict[str, Any] = {}
    for part in parts:
        for key, value in part.items():
            if key in combined and key in SCHEMA_ANNOTATIONS:
                continue
            if key in combined and canonical_json(combined[key]) != canonical_json(value):
                if key == "properties" and schemas_match(
                    {"properties": combined[key]}, {"properties": value},
                ):
                    continue
                return {"allOf": parts}
            combined[key] = value
    return combined


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
                if not is_schema or key not in SCHEMA_ANNOTATIONS | {"$defs", "$ref"}
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
