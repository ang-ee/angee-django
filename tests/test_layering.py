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
BASE_SERVING_IMPORTS = (
    "angee.base.conf",
    "angee.base.addons",
    "angee.server",
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
)


def test_base_server_import_closure_stays_vendor_free() -> None:
    """Importing the framework packages reaches none of the moved addon vendors."""

    script = "\n".join(
        (
            "import importlib, json, sys",
            "from django.conf import settings",
            "settings.configure(INSTALLED_APPS=[], SECRET_KEY='core-serving-import', ANGEE_WEB_STATEMENT_TIMEOUT=0)",
            f"for name in {BASE_SERVING_IMPORTS!r}:",
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
        for root in (PROJECT_ROOT / "addons" / "angee",)
        for path in sorted(root.rglob("*.py"))
    }
    assert not {path: names for path, names in violations.items() if names}


def test_addon_serving_modules_never_import_build_owners() -> None:
    """Serving modules cannot load composition; declared build and typing owners may."""
    root = PROJECT_ROOT / "addons/angee"
    typing_paths = {root / "base/typing.py", root / "provenance/typing.py"}
    build_paths = set(root.glob("*/build.py")) | typing_paths
    fixture_paths = {root / "base/testing/permissions.py", root / "base/testing/fixtures.py"}
    build_modules = {"django_compose.build", "django_compose.model_composition", "django_compose.rendering"} | {
        "angee." + ".".join(path.relative_to(root).with_suffix("").parts)
        for path in build_paths
    }
    violations = {
        path.relative_to(PROJECT_ROOT).as_posix(): sorted(
            name for name in _module_imports(path, runtime_only=True)
            if name == "angee.compose" or name.startswith("angee.compose.")
            or any(name == module or name.startswith(module + ".") for module in build_modules)
        )
        for path in root.rglob("*.py")
        if "tests" not in path.relative_to(root).parts and path not in build_paths | fixture_paths
    }
    assert not {path: names for path, names in violations.items() if names}


def test_addon_test_harness_does_not_import_the_composer() -> None:
    """Bare source fixtures remain independent of the generic build engine."""
    root = PROJECT_ROOT / "addons/angee/workflows/testing"
    violations = {
        path.relative_to(PROJECT_ROOT).as_posix(): sorted(
            name for name in _module_imports(path, runtime_only=True)
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
        for root in (PROJECT_ROOT / "addons/angee",)
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
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "tests.settings"},
        check=True,
        capture_output=True,
        text=True,
    )
    closure = set(json.loads(result.stdout.splitlines()[-1]))

    assert {
        sdk for sdk in INFERENCE_VENDOR_SDKS if any(module == sdk or module.startswith(f"{sdk}.") for module in closure)
    } == set()


def test_generated_host_startup_and_provision_dispatch_never_load_the_compiler(tmp_path: Path) -> None:
    """A verified native host retains late deployment values without model compilation."""

    from tests.composed_host import COMPOSED_TEST_TIMEOUT

    environment = {
        **{key: value for key, value in os.environ.items() if key not in {
            "DJANGO_SETTINGS_MODULE", "DJANGO_COMPOSE_MODE", "DATABASE_URL", "YAMLCONF_CONFFILE", "SENTRY_DSN",
        }},
    }
    bootstrap = (
        "from pathlib import Path; from tests.composed_host import boot; "
        f"boot(Path({str(PROJECT_ROOT)!r}), Path({str(tmp_path / 'runtime')!r}), [], "
        "include_examples=False, root_apps=['angee.base'])"
    )
    subprocess.run(
        [sys.executable, "-c", bootstrap], cwd=PROJECT_ROOT, env=environment,
        check=True, capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    script = """
import importlib.abc,json,os,sys
from pathlib import Path
root=Path(sys.argv[1]); sys.path.insert(0,str(root))
os.environ['ANGEE_PROJECT_DIR']=str(root)
os.environ['DJANGO_SETTINGS_MODULE']='composed_test_settings'
os.environ['YAMLCONF_SECRET_KEY']='deployment-secret'
os.environ['CELERY_BROKER_URL']='redis://deployment.invalid:6379/3'
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  blocked={'django_compose.build','django_compose.model_composition','django_compose.rendering'}
  if any(fullname==name or fullname.startswith(name+'.') for name in blocked):
   raise AssertionError('serving imported model compilation: '+fullname)
sys.meta_path.insert(0,Boundary())
from django.db.backends.base.base import BaseDatabaseWrapper
def forbidden(*args,**kwargs):raise AssertionError('startup opened a database')
BaseDatabaseWrapper.ensure_connection=forbidden
import django
django.setup()
from django.apps import apps
from django.conf import settings
from django.core.management import get_commands,load_command_class
from angee.base.addons import addon_facts
assert settings.SECRET_KEY=='deployment-secret'
assert settings.CELERY_BROKER_URL=='redis://deployment.invalid:6379/3'
assert apps.is_installed('angee.base')
assert addon_facts('angee.base','angee.platform')['category']=='Foundation'
assert get_commands()['provision']=='angee.base'
command=load_command_class('angee.base','provision')
assert type(command).__module__=='angee.base.management.commands.provision'
print(json.dumps({'deployment_overrides':'retained','provision_owner':type(command).__module__}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], cwd=PROJECT_ROOT, env=environment,
        capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["deployment_overrides"] == "retained"
