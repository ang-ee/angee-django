"""Guard the one-way dependency direction of Angee backend packages."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANGEE = ROOT / "src/angee"
BASE = ANGEE / "base"
SERVER = ANGEE / "server"
SERVING_MANAGEMENT = tuple(SERVER / "management/commands" / name for name in ("runserver.py", "serve.py"))
GRAPHQL = ANGEE / "graphql"
RESOURCES = ANGEE / "resources"
SOURCE_ROOTS = (ROOT / "src",)
ADDON_ROOTS = (ANGEE,)

# Derived from the source tree so a new base addon is guarded automatically.
_ADDON_PACKAGES = tuple(
    f"angee.{path.name}"
    for addon_root in ADDON_ROOTS
    for metadata in sorted(addon_root.glob("*/pyproject.toml"))
    if (path := metadata.parent).is_dir()
)


def _module_imports(path: Path, *, runtime_only: bool = False) -> set[str]:
    """Resolve source imports; runtime guards exclude native TYPE_CHECKING branches."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    module = _module_name(path)
    names: set[str] = set()

    class Imports(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            condition = node.test
            type_only = (
                isinstance(condition, ast.Name) and condition.id == "TYPE_CHECKING"
                or isinstance(condition, ast.Attribute) and condition.attr == "TYPE_CHECKING"
            )
            if runtime_only and type_only:
                for child in node.orelse:
                    self.visit(child)
            else:
                self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            names.update(alias.name for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level:
                package = module if path.name == "__init__.py" else module.rpartition(".")[0]
                imported = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
            else:
                imported = node.module or ""
            if imported:
                names.add(imported)
                names.update(f"{imported}.{alias.name}" for alias in node.names if alias.name != "*")

    Imports().visit(tree)
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


def _tree_imports(root: Path, *, runtime_only: bool = False) -> set[str]:
    """Return the union of imports across a package subtree."""

    paths = (root,) if root.is_file() else tuple(root.rglob("*.py"))
    assert paths, f"no Python sources found under {root}"
    names: set[str] = set()
    for path in paths:
        names |= _module_imports(path, runtime_only=runtime_only)
    return names


@pytest.mark.parametrize(
    ("source_path", "source", "expected"),
    [
        (
            "src/angee/base/models/base.py",
            "from . import merge",
            {"angee.base.models", "angee.base.models.merge"},
        ),
        (
            "src/angee/example/models.py",
            "from angee.base import models as base_models",
            {"angee.base", "angee.base.models"},
        ),
        (
            "src/angee/example/__init__.py",
            "from angee.base.models.audit import retained_set_null",
            {
                "angee.base.models.audit",
                "angee.base.models.audit.retained_set_null",
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


def test_foundation_runtime_is_below_all_capability_addons() -> None:
    """Foundation serving code has no composer or product-addon dependency."""

    imports = set().union(*(
        _module_imports(path, runtime_only=True) for path in BASE.rglob("*.py")
        if path.name not in {"build.py", "typing.py"} and "testing" not in path.relative_to(BASE).parts
    ))
    forbidden = (
        "angee.compose", "django_compose.build", "django_compose.model_composition", "django_compose.rendering",
        *(name for name in _ADDON_PACKAGES if name != "angee.base"),
    )
    assert not any(name == prefix or name.startswith(prefix + ".") for name in imports for prefix in forbidden)


def test_framework_has_no_private_host_or_composer_tree() -> None:
    """The namespace is distributed by addons; generic composition has one upstream owner."""

    assert not (ROOT / "angee").exists()
    assert not list((ROOT / "core").rglob("*.py"))
    assert not (ANGEE / "compose").exists()


def test_production_sources_do_not_import_test_support() -> None:
    """Addon test compositions and generic fixtures stay test-only."""

    testing_packages = ("tests", "angee.base.testing", *(f"{addon}.testing" for addon in _ADDON_PACKAGES))
    violations = {}
    for root in ADDON_ROOTS:
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

    forbidden = ("angee.apps", "angee.base.apps.BaseConfig")
    violations = {
        str(path.relative_to(ROOT)): imports
        for addon_root in ADDON_ROOTS
        for path in sorted(addon_root.rglob("apps.py"))
        if (imports := sorted(name for name in _module_imports(path)
                              if any(name.startswith(prefix) for prefix in forbidden)))
    }
    assert not violations


def test_resources_serving_does_not_import_compose() -> None:
    """Resources contributes through build.py; serving never imports that adapter."""

    imports = set().union(*(
        _module_imports(path, runtime_only=True) for path in RESOURCES.rglob("*.py") if path.name != "build.py"
    ))
    assert not any(name.startswith((
        "angee.compose", "angee.resources.build", "django_compose.build",
        "django_compose.model_composition", "django_compose.rendering",
    )) for name in imports)


def test_graphql_does_not_import_compose() -> None:
    """The GraphQL runtime does not import build-time compose code."""

    imports = _tree_imports(GRAPHQL, runtime_only=True)
    assert not any(name.startswith((
        "angee.compose", "django_compose.build", "django_compose.model_composition", "django_compose.rendering",
    )) for name in imports)


def test_stable_serving_entrypoints_do_not_import_compose() -> None:
    """Serving entrypoints use Django's populated registry, not compose."""

    imports = set().union(*(
        _module_imports(path) for path in (SERVER / "urls.py", SERVER / "asgi.py", *SERVING_MANAGEMENT)
    ))
    forbidden = (
        "angee.compose", "django_compose.build", "django_compose.model_composition", "django_compose.rendering",
    )
    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


@pytest.mark.parametrize("runtime_only", [False, True])
def test_import_guard_distinguishes_type_only_dependencies(monkeypatch, runtime_only):
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from django_compose.model_composition import ModelComposition\n"
        "else:\n    from angee.base.paths import resolve_path\n"
    ))
    imports = _module_imports(BASE / "typing.py", runtime_only=runtime_only)
    assert ("django_compose.model_composition" in imports) is not runtime_only
    assert "angee.base.paths.resolve_path" in imports
