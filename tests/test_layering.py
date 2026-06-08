"""Guard the one-way dependency direction of Angee backend packages."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANGEE = ROOT / "angee"
BASE = ANGEE / "base"
GRAPHQL = ANGEE / "graphql"
COMPOSE = ANGEE / "compose"
RESOURCES = ROOT / "addons" / "angee" / "resources"  # resources is a base addon

# Derived from the directory so a new base addon is guarded automatically.
_ADDON_PACKAGES = tuple(
    f"angee.{path.name}" for path in sorted((ROOT / "addons" / "angee").iterdir()) if path.is_dir()
)


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


def test_base_is_the_model_layer_below_all_siblings() -> None:
    """base (the model toolkit) imports no sibling subsystem or addon."""

    imports = _tree_imports(BASE)
    forbidden = ("angee.compose", "angee.graphql", *_ADDON_PACKAGES)
    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_no_shared_addon_config_base_module() -> None:
    """Addons use plain Django AppConfig attributes, not an Angee subclass."""

    assert not (ANGEE / "apps.py").exists()


def test_resources_does_not_import_compose() -> None:
    """The resource subsystem does not import build-time compose code."""

    imports = _tree_imports(RESOURCES)
    assert not any(name.startswith("angee.compose") for name in imports)


def test_graphql_does_not_import_compose() -> None:
    """The GraphQL runtime does not import build-time compose code."""

    imports = _tree_imports(GRAPHQL)
    assert not any(name.startswith("angee.compose") for name in imports)


def test_stable_serving_entrypoints_do_not_import_compose() -> None:
    """Serving entrypoints use Django's populated registry, not compose."""

    imports = _module_imports(ANGEE / "urls.py") | _module_imports(ANGEE / "asgi.py")
    forbidden = ("angee.compose",)
    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_compose_has_no_rebac_permission_renderer() -> None:
    """Per-addon REBAC schemas stay with their owning apps."""

    assert not (COMPOSE / "rebac.py").exists()
