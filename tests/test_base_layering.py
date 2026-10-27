"""Guard the one-way dependency direction of Angee backend packages."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE_SPEC = importlib.util.find_spec("angee")
assert BASE_SPEC is not None and BASE_SPEC.origin is not None
ANGEE = Path(BASE_SPEC.origin).resolve().parent
MODEL_TOOLKIT = (
    *(ANGEE / name for name in (
        "actors.py", "apps.py", "autoconfig.py", "changesets.py", "checks.py",
        "errors.py", "evidence.py", "fields.py", "identity.py", "impl.py",
        "indexes.py", "jsonschema.py", "lineage.py", "middleware.py", "numeric.py",
        "pagination.py", "permissions.py", "refs.py", "serialization.py", "signals.py",
        "tiers.py", "validation.py",
    )),
    ANGEE / "management",
    ANGEE / "models",
)
GRAPHQL = ROOT / "addons" / "angee" / "graphql"
COMPOSE = ANGEE / "compose"
RESOURCES = ROOT / "addons" / "angee" / "resources"  # resources is a base addon
# Resolve the nested addon import root before its enclosing repository root.
SOURCE_ROOTS = (ROOT / "addons", ANGEE.parent)
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
                package = module if path.name == "__init__.py" else module.rpartition(".")[0]
                imported = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
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

    paths = (root,) if root.is_file() else tuple(root.rglob("*.py"))
    assert paths, f"no Python sources found under {root}"
    names: set[str] = set()
    for path in paths:
        names |= _module_imports(path)
    return names


@pytest.mark.parametrize(
    ("source_path", "source", "expected"),
    [
        (
            "angee/models/base.py",
            "from . import merge",
            {"angee.models", "angee.models.merge"},
        ),
        (
            "addons/angee/example/models.py",
            "from angee import models as base_models",
            {"angee", "angee.models"},
        ),
        (
            "addons/angee/example/__init__.py",
            "from angee.models import retained_set_null",
            {
                "angee.models",
                "angee.models.retained_set_null",
            },
        ),
    ],
)
def test_relative_imports_resolve_against_the_source_import_root(
    monkeypatch: pytest.MonkeyPatch, source_path: str, source: str, expected: set[str]
) -> None:
    """Core and addon relative imports cannot bypass dependency guards."""

    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: source)
    assert _module_imports(ROOT / source_path) == expected


def test_root_model_toolkit_is_below_all_subsystems() -> None:
    """The root model toolkit imports no sibling subsystem or addon."""

    imports = set().union(*(_tree_imports(path) for path in MODEL_TOOLKIT))
    forbidden = ("angee.compose", "angee.graphql", *_ADDON_PACKAGES)
    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_core_does_not_import_folder_addons() -> None:
    """The core wheel remains importable without folder-addon sources."""

    violations = {
        str(path.relative_to(ROOT)): forbidden
        for path in sorted(ANGEE.rglob("*.py"))
        if (
            forbidden := sorted(
                name
                for name in _module_imports(path)
                if any(name == addon or name.startswith(f"{addon}.") for addon in _ADDON_PACKAGES)
            )
        )
    }
    assert not violations


def test_production_sources_do_not_import_test_support() -> None:
    """Addon test compositions and generic fixtures stay test-only."""

    testing_packages = ("tests", "angee.testing", *(f"{addon}.testing" for addon in _ADDON_PACKAGES))
    violations = {}
    for root in (ANGEE, ROOT / "addons"):
        for path in sorted(root.rglob("*.py")):
            if (
                {"testing", "tests"}.intersection(path.relative_to(ROOT).parts)
                or path.name in {"conftest.py", "tests.py"}
                or path.name.startswith(("test_", "tests_"))
            ):
                continue
            forbidden = sorted(
                name
                for name in _module_imports(path)
                if any(name == package or name.startswith(f"{package}.") for package in testing_packages)
            )
            if forbidden:
                violations[str(path.relative_to(ROOT))] = forbidden
    assert not violations


def test_addons_do_not_use_a_shared_app_config_subclass() -> None:
    """Addons use plain Django AppConfig attributes, not the foundation config."""

    violations = {
        str(path.relative_to(ROOT)): sorted(
            name for name in _module_imports(path) if name.startswith("angee.apps")
        )
        for addon_root in ADDON_ROOTS
        for path in sorted(addon_root.rglob("apps.py"))
        if any(name.startswith("angee.apps") for name in _module_imports(path))
    }
    assert not violations


def test_resources_serving_does_not_import_compose() -> None:
    """Resources contributes through build.py; serving never imports that adapter."""

    imports = set().union(*(
        _module_imports(path) for path in RESOURCES.rglob("*.py") if path.name != "build.py"
    ))
    assert not any(name.startswith(("angee.compose", "angee.resources.build")) for name in imports)


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
# Pre-consolidation base-addon side of pre-90bdd58b tests/test_layering.py;
# kept separate because it guards addon dependency direction.
