"""Guard the one-way dependency direction of the base addon.

The build package (``angee.base.compose``) emits the runtime; nothing on the
runtime/serving path may import it. Discovery is the shared primitive and must
stay free of higher layers.
"""

from __future__ import annotations

import ast
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "src" / "angee" / "base"


def _module_imports(path: Path) -> set[str]:
    """Return every dotted module name imported by one source file."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _tree_imports(root: Path) -> set[str]:
    """Return the union of imports across a package subtree."""

    names: set[str] = set()
    for path in root.rglob("*.py"):
        names |= _module_imports(path)
    return names


def test_graphql_does_not_import_compose() -> None:
    imports = _tree_imports(BASE / "graphql")
    assert not any(name.startswith("angee.base.compose") for name in imports)


def test_resources_does_not_import_compose() -> None:
    imports = _tree_imports(BASE / "resources")
    assert not any(name.startswith("angee.base.compose") for name in imports)


def test_serving_does_not_import_compose() -> None:
    imports = _module_imports(BASE / "asgi.py")
    imports |= _module_imports(BASE / "urls.py")
    imports |= _module_imports(BASE / "views.py")
    imports |= _module_imports(BASE / "consumers.py")
    imports |= _module_imports(BASE / "signals.py")
    assert not any(name.startswith("angee.base.compose") for name in imports)


def test_discovery_depends_only_on_apps() -> None:
    imports = _module_imports(BASE / "discovery.py")
    internal = {name for name in imports if name.startswith("angee.base")}
    assert internal <= {"angee.base.apps"}
