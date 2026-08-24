"""Guard the one-way dependency direction of Angee backend packages."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_SPEC = importlib.util.find_spec("angee.base")
assert BASE_SPEC is not None and BASE_SPEC.origin is not None
ANGEE = Path(BASE_SPEC.origin).resolve().parents[1]
BASE = ANGEE / "base"
GRAPHQL = ANGEE / "graphql"
COMPOSE = ANGEE / "compose"
RESOURCES = ROOT / "addons" / "angee" / "resources"  # resources is a base addon
SOURCE_ROOTS = (ANGEE.parent, ROOT / "addons")
ADDON_ROOTS = (ROOT / "addons" / "angee",)

# Derived from the source tree so a new base addon is guarded automatically.
_ADDON_PACKAGES = tuple(
    f"angee.{path.name}"
    for addon_root in ADDON_ROOTS
    for path in sorted(addon_root.iterdir())
    if path.is_dir()
)


def _module_imports(path: Path) -> set[str]:
    """Return every dotted module name imported by one source file."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    module = _module_name(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
                prefix = package[: len(package) - node.level + 1]
                imported = ".".join((*prefix, *(node.module or "").split(".")))
            else:
                imported = node.module or ""
            if imported:
                names.add(imported)
                names.update(f"{imported}.{alias.name}" for alias in node.names if alias.name != "*")
    return names


def _module_name(path: Path) -> str:
    """Return the importable module name for one repository-owned Python source."""

    for source_root in SOURCE_ROOTS:
        try:
            relative = path.relative_to(source_root).with_suffix("")
        except ValueError:
            continue
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        return ".".join(parts)
    raise ValueError(f"{path} is outside the repository's Python source roots")


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
