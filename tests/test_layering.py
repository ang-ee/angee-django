"""Guard the one-way dependency direction of Angee backend packages."""

from __future__ import annotations

import ast
from pathlib import Path

from angee.base.apps import BaseAddonConfig

SRC = Path(__file__).resolve().parents[1] / "src" / "angee"
BASE = SRC / "base"
RESOURCES = SRC / "resources"


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


def test_base_does_not_import_sibling_packages() -> None:
    """The runtime base package stays below compose and resources."""

    imports = _tree_imports(BASE)
    assert not any(name.startswith("angee.compose") for name in imports)
    assert not any(name.startswith("angee.resources") for name in imports)


def test_addons_use_conventional_model_sources_only() -> None:
    """Addon source models come from the addon's own models module."""

    assert not hasattr(BaseAddonConfig, "source_model_modules")


def test_resources_does_not_import_compose() -> None:
    """The resource subsystem does not import build-time compose code."""

    imports = _tree_imports(RESOURCES)
    assert not any(name.startswith("angee.compose") for name in imports)


def test_serving_does_not_import_compose() -> None:
    """Serving modules do not import build-time compose code."""

    imports = _module_imports(BASE / "asgi.py")
    imports |= _module_imports(BASE / "urls.py")
    imports |= _module_imports(BASE / "views.py")
    imports |= _module_imports(BASE / "consumers.py")
    imports |= _module_imports(BASE / "signals.py")
    assert not any(name.startswith("angee.compose") for name in imports)


def test_discovery_depends_only_on_apps() -> None:
    """Addon discovery depends only on the AppConfig contract."""

    imports = _module_imports(BASE / "discovery.py")
    internal = {name for name in imports if name.startswith("angee.base")}
    assert internal <= {"angee.base.apps"}
