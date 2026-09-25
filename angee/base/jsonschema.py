"""Bounded JSON Schema reference resolution shared by core and addons."""

from typing import Any

from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012


class LocalSchemaReferences:
    """Resolve root pointers and anchors; remote and nested resource scopes are unsupported."""

    def __init__(self, root: Any) -> None:
        self.root = root
        self.resolver = Registry().resolver_with_root(DRAFT202012.create_resource(root))

    def resolve(self, reference: Any) -> Any:
        if isinstance(reference, str) and reference.startswith("#"):
            try:
                resolved = self.resolver.lookup(reference)
                if resolved.resolver.lookup("#").contents is self.root:
                    return resolved.contents
            except Unresolvable:
                pass
        return None
