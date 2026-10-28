"""Guard framework import boundaries and optional state-field declarations."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from angee.messaging import identity
from tests.test_base_layering import _module_imports

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_SERVING_IMPORTS = (
    "angee.asgi",
    "angee",
    "angee.compose",
)
RESOURCE_CONTRACT_IMPORTS = (
    "angee.graphql.data",
    "angee.graphql.data.contracts",
)
REMOVED_VENDOR_MODULES = (
    "anthropic",
    "anymail",
    "authlib",
    "axes",
    "channels_redis",
    "croniter",
    "cryptg",
    "dateutil",
    "discord",
    "fastmcp",
    "httpcore",
    "httpcore2",
    "httpx",
    "httpx2",
    "imapclient",
    "import_export",
    "jwt",
    "magic",
    "mailparser_reply",
    "markdown_it",
    "mcp",
    "neonize",
    "openai",
    "phonenumbers",
    "pydantic",
    "pydantic_ai",
    "qrcode",
    "ruamel",
    "slack_sdk",
    "strawberry",
    "strawberry_django",
    "strawberry_django_aggregates",
    "strawberry_django_hasura",
    "tablib",
    "telethon",
    "vobject",
    "yaml",
)


def test_core_serving_import_closure_stays_vendor_free() -> None:
    """Importing the framework packages reaches none of the moved addon vendors."""

    script = "\n".join(
        (
            "import importlib, json, sys",
            "from django.conf import settings",
            "settings.configure(INSTALLED_APPS=[], SECRET_KEY='core-serving-import', ANGEE_WEB_STATEMENT_TIMEOUT=0)",
            f"for name in {CORE_SERVING_IMPORTS!r}:",
            "    importlib.import_module(name)",
            "print(json.dumps(sorted(sys.modules)))",
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    closure = set(json.loads(result.stdout))

    reached = {
        vendor
        for vendor in REMOVED_VENDOR_MODULES
        if any(module == vendor or module.startswith(f"{vendor}.") for module in closure)
    }
    assert reached == set()


def test_resource_contract_import_closure_stays_projection_free() -> None:
    """GraphQL wire declarations load without producers, Strawberry, or money."""

    script = "\n".join(
        (
            "import importlib, json, sys",
            f"sys.path.insert(0, {str(PROJECT_ROOT / "addons")!r})",
            "from django.conf import settings",
            "settings.configure(INSTALLED_APPS=[])",
            "import django",
            "django.setup()",
            f"for name in {RESOURCE_CONTRACT_IMPORTS!r}:",
            "    importlib.import_module(name)",
            "print(json.dumps(sorted(sys.modules)))",
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    closure = set(json.loads(result.stdout))

    reached = {
        module
        for module in closure
        if module in {"angee.graphql.data.metadata", "angee.graphql.data.resource_fields", "angee.graphql.data.hasura"}
        or module == "angee.money"
        or module.startswith("angee.money.")
        or module == "strawberry"
        or module.startswith("strawberry.")
    }
    assert reached == set()


def test_record_pointer_import_closure_reaches_only_the_identity_model_contract() -> None:
    """Public record projection imports identity without the composed model toolkit."""

    script = "\n".join(
        (
            "import importlib, json, sys",
            f"sys.path.insert(0, {str(PROJECT_ROOT / "addons")!r})",
            "from django.conf import settings",
            "settings.configure(INSTALLED_APPS=['django.contrib.contenttypes'])",
            "import django",
            "django.setup()",
            "importlib.import_module('angee.base.refs')",
            "print(json.dumps(sorted(sys.modules)))",
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    closure = set(json.loads(result.stdout))

    assert "angee.base.refs" in closure
    assert {
        module for module in closure
        if module == "angee.base.models" or module.startswith("angee.base.models.")
    } == {"angee.base.models", "angee.base.models.identity"}


def test_integrate_does_not_import_workflows() -> None:
    """Record truth stays independent of optional workflow execution composition."""

    root = PROJECT_ROOT / "addons" / "angee" / "integrate"
    violations = {
        str(path.relative_to(PROJECT_ROOT)): sorted(
            name for name in _module_imports(path)
            if name == "angee.workflows" or name.startswith(("angee.workflows.", "angee.workflows_"))
        )
        for path in sorted(root.rglob("*.py"))
    }
    assert not {path: names for path, names in violations.items() if names}


def test_messaging_identity_public_owner() -> None:
    """Bridge identity adapters have a vendor-free public coercion owner."""

    for name in ("mapping", "millis_to_utc", "sequence", "text"):
        public = getattr(identity, name)
        assert public.__module__ == "angee.messaging.identity"
    assert identity.mapping({"id": 1}) == {"id": 1}
    assert identity.mapping("id") is None
    assert identity.sequence([1, 2]) == [1, 2]
    assert identity.sequence("id") == ()
    assert identity.text("  id  ") == "id"
    assert identity.millis_to_utc(1000) == datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert identity.millis_to_utc("invalid") is None

def test_framework_does_not_import_retired_ownership_owner() -> None:
    """Framework code never imports the retired ownership owner."""

    forbidden = ("angee.integrate.ownership",)
    violations = {
        str(path.relative_to(PROJECT_ROOT)): sorted(
            name
            for name in _module_imports(path)
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden)
        )
        for root in (PROJECT_ROOT / "angee", PROJECT_ROOT / "addons" / "angee")
        for path in sorted(root.rglob("*.py"))
    }
    assert not {path: names for path, names in violations.items() if names}


def test_addon_serving_modules_never_import_build_owners() -> None:
    """Serving modules cannot load composition; declared build and typing owners may."""
    root = PROJECT_ROOT / "addons/angee"
    typing_paths = {root / "base/typing.py", root / "provenance/typing.py"}
    build_paths = set(root.glob("*/build.py")) | typing_paths
    fixture_paths = {root / "base/testing/permissions.py", root / "base/testing/fixtures.py"}
    build_modules = {"angee.base.build", "angee.web"} | {
        "angee." + ".".join(path.relative_to(root).with_suffix("").parts)
        for path in build_paths
    }
    violations = {
        path.relative_to(PROJECT_ROOT).as_posix(): sorted(
            name for name in _module_imports(path)
            if name == "angee.compose" or name.startswith("angee.compose.")
            or any(name == module or name.startswith(module + ".") for module in build_modules)
        )
        for path in root.rglob("*.py")
        if "tests" not in path.relative_to(root).parts and path not in build_paths | fixture_paths
    }
    assert not {path: names for path, names in violations.items() if names}


def test_addon_test_harness_does_not_import_the_composer() -> None:
    """Build-time permission composition belongs to core test support."""
    root = PROJECT_ROOT / "addons/angee/workflows/testing"
    violations = {
        path.relative_to(PROJECT_ROOT).as_posix(): sorted(
            name for name in _module_imports(path)
            if name == "angee.compose" or name.startswith("angee.compose.")
        )
        for path in root.rglob("*.py")
    }
    assert not {path: names for path, names in violations.items() if names}


def _production_sources(root: Path) -> Iterator[tuple[Path, ast.Module]]:
    """Read active Python sources, excluding migrations and test probes."""

    for path in sorted(root.rglob("*.py")):
        if (
            {"migrations", "runtime_migrations", "tests"}.intersection(path.relative_to(root).parts)
            or path.stem == "tests"
            or path.stem.startswith("test_")
        ):
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _blank_state_fields_without_null(tree: ast.Module) -> Iterator[int]:
    """Check direct and qualified StateField calls with literal blank=True."""

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, (ast.Name, ast.Attribute)):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        if name != "StateField":
            continue
        enabled = {
            keyword.arg
            for keyword in node.keywords
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is True
        }
        if "blank" in enabled and "null" not in enabled:
            yield node.lineno


def test_blank_state_fields_use_null() -> None:
    """Active optional enums use NULL so native GraphQL auto emits nullable enums."""

    violations = [
        f"{path.relative_to(PROJECT_ROOT).as_posix()}:{line}"
        for root in (PROJECT_ROOT / "angee", PROJECT_ROOT / "addons/angee")
        for path, tree in _production_sources(root)
        for line in _blank_state_fields_without_null(tree)
    ]
    assert not violations, "Declare blank StateFields with null=True:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ("StateField(blank=True)", [2]),
        ("fields.StateField(blank=True, null=False)", [2]),
        ("StateField(blank=True, null=None)", [2]),
        ("StateField(blank=True, null=True)", []),
        ("StateField(blank=False)", []),
        ("StateField()", []),
        ("CharField(blank=True)", []),
    ],
)
def test_blank_state_field_syntax(declaration: str, expected: list[int]) -> None:
    tree = ast.parse(f"class Example:\n    state = {declaration}\n")
    assert list(_blank_state_fields_without_null(tree)) == expected


INFERENCE_BACKEND_MODULES = (
    "angee.agents_integrate_anthropic.backend",
    "angee.agents_integrate_ollama.backend",
    "angee.agents_integrate_openai.backend",
)
INFERENCE_VENDOR_SDKS = ("anthropic", "openai")


def test_inference_backends_catalogue_without_their_vendor_sdks() -> None:
    """Listing and labelling provider backends loads no vendor SDK; binding a model does."""

    script = "\n".join(
        (
            "import django, importlib, json, sys",
            "django.setup()",
            "from angee.agents.backends import InferenceBackend",
            "from angee.base.implementations import check_impl_registry, impl_choices",
            f"for name in {INFERENCE_BACKEND_MODULES!r}:",
            "    importlib.import_module(name)",
            "impl_choices(InferenceBackend)",
            "check_impl_registry(InferenceBackend)",
            "print(json.dumps(sorted(sys.modules)))",
        )
    )
    paths = (PROJECT_ROOT, PROJECT_ROOT / "addons", PROJECT_ROOT / "examples" / "addons")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "tests.settings", "PYTHONPATH": os.pathsep.join(map(str, paths))},
        check=True,
        capture_output=True,
        text=True,
    )
    closure = set(json.loads(result.stdout.splitlines()[-1]))

    assert {
        sdk for sdk in INFERENCE_VENDOR_SDKS if any(module == sdk or module.startswith(f"{sdk}.") for module in closure)
    } == set()
