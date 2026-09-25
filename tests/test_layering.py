"""Guard framework import boundaries and optional state-field declarations."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from angee.messaging import _wire, identity
from angee.workflows_integrate import archive_steps, steps
from tests.test_base_layering import _module_imports

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_SERVING_IMPORTS = (
    "angee.asgi",
    "angee.base",
    "angee.compose",
    "angee.jobs",
)
DATA_CONTRACT_IMPORTS = (
    "angee.data",
    "angee.data.field_classification",
    "angee.data.metadata",
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
    "httpx",
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


def test_data_contract_import_closure_stays_transport_neutral() -> None:
    """The data description contract reaches neither GraphQL, Strawberry, nor money."""

    script = "\n".join(
        (
            "import importlib, json, sys",
            "from django.conf import settings",
            "settings.configure(INSTALLED_APPS=[])",
            "import django",
            "django.setup()",
            f"for name in {DATA_CONTRACT_IMPORTS!r}:",
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
        if module == "angee.graphql"
        or module.startswith("angee.graphql.")
        or module == "angee.money"
        or module.startswith("angee.money.")
        or module == "strawberry"
        or module.startswith("strawberry.")
    }
    assert reached == set()


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


def test_workflows_integrate_public_archive_extension_imports() -> None:
    """Released bridge extractors share the framework's canonical archive contracts."""

    assert steps.ArchiveExtractor is archive_steps.ArchiveExtractor
    assert steps.ArchiveExecutionReporter is archive_steps.ArchiveExecutionReporter


def test_messaging_identity_public_owner() -> None:
    """Bridge identity adapters have a vendor-free public coercion owner."""

    for name in ("mapping", "millis_to_utc", "sequence", "text"):
        public = getattr(identity, name)
        assert public.__module__ == "angee.messaging.identity"
        assert getattr(_wire, name) is public
    assert identity.mapping({"id": 1}) == {"id": 1}
    assert identity.mapping("id") is None
    assert identity.sequence([1, 2]) == [1, 2]
    assert identity.sequence("id") == ()
    assert identity.text("  id  ") == "id"
    assert identity.millis_to_utc(1000) == datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert identity.millis_to_utc("invalid") is None

    violations = {
        str(path.relative_to(PROJECT_ROOT))
        for path in sorted((PROJECT_ROOT / "addons" / "angee").rglob("*.py"))
        if "angee.messaging._wire" in _module_imports(path)
    }
    assert not violations


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


def test_source_does_not_import_historical_relationships() -> None:
    """Only external materialized history and tests may use the frozen API."""

    module = "angee.base.historical_relationships"
    violations = {}
    for directory in ("angee", "addons", "examples", "templates"):
        for path in sorted((PROJECT_ROOT / directory).rglob("*.py")):
            relative = path.relative_to(PROJECT_ROOT)
            imports = sorted(name for name in _module_imports(path) if name == module or name.startswith(f"{module}."))
            if imports:
                violations[str(relative)] = imports
    assert not violations


def _production_sources(root: Path) -> Iterator[tuple[Path, ast.Module]]:
    """Read active Python sources, preserving migration history and test probes."""

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
