"""Guard framework import boundaries and ownership of database routing seams."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

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


class _FKReload(NamedTuple):
    """One syntactic reload, also used by read-only addon sweep inventories."""

    path: Path
    line: int
    function: str
    manager: str
    model: str
    pk: str
    required: bool


def _fk_reloads(root: Path) -> Iterator[_FKReload]:
    """Find direct single-row FK reloads without importing production modules.

    Scan core and framework addons, excluding migrations and tests. Match direct
    ``objects``, ``_default_manager`` and ``_base_manager`` calls ending in
    ``get(pk=row.<field>_id)`` or ``filter(pk=row.<field>_id).first()``,
    optionally bound with ``using``/``db_manager`` and ``select_related``/``all``.
    Typing-only ``cast`` wrappers do not hide a manager.
    Generic ``object_id`` references, same-row reloads, projections, locks,
    scoped/custom queries and indirect ID/queryset variables are outside
    this deliberately syntactic guard; it does not infer write paths or dataflow.
    """

    for path in sorted(root.rglob("*.py")):
        if (
            {"migrations", "runtime_migrations", "tests"}.intersection(path.relative_to(root).parts)
            or path.stem == "tests"
            or path.stem.startswith("test_")
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions = [part for part in ast.walk(tree) if isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            lookup: ast.AST = node
            if node.func.attr == "first" and not node.args and not node.keywords:
                lookup = node.func.value
            elif node.func.attr != "get":
                continue
            if (
                not isinstance(lookup, ast.Call)
                or not isinstance(lookup.func, ast.Attribute)
                or lookup.func.attr not in {"get", "filter"}
                or lookup.args
                or len(lookup.keywords) != 1
                or lookup.keywords[0].arg != "pk"
            ):
                continue
            manager = lookup.func.value
            while isinstance(manager, ast.Call):
                if isinstance(manager.func, ast.Attribute) and manager.func.attr in {
                    "using", "db_manager", "select_related", "all"
                }:
                    manager = manager.func.value
                elif isinstance(manager.func, ast.Name) and manager.func.id == "cast" and len(manager.args) == 2:
                    manager = manager.args[1]
                else:
                    break
            if not isinstance(manager, ast.Attribute) or manager.attr not in {
                "objects", "_default_manager", "_base_manager"
            }:
                continue
            target = lookup.keywords[0].value
            if not isinstance(target, ast.Attribute) or not target.attr.endswith("_id") or target.attr == "object_id":
                continue
            enclosing = [part for part in functions if part.lineno <= node.lineno <= (part.end_lineno or part.lineno)]
            function = max(enclosing, key=lambda part: part.lineno).name if enclosing else ""
            yield _FKReload(
                path, node.lineno, function, manager.attr, ast.unparse(manager.value), ast.unparse(target),
                lookup.func.attr == "get",
            )


_FK_RELOAD_EXEMPTIONS = {
    ("addons/angee/parties/models.py", "canonical", "_base_manager", "type(self)", "party.merged_into_id"):
        "Preserve Person/Organization's concrete subtype instead of the FK's Party target.",
    ("addons/angee/parties/connections.py", "_connection_person", "_base_manager", "person_model", "handle.party_id"):
        "Project a Party FK onto Person rather than returning the declared Party target.",
    ("addons/angee/projects/models.py", "delete", "objects", "project_model", "binding.project_id"):
        "The actor-scoped project lookup gates binding deletion with PermissionDenied.",
    ("addons/angee/integrate/connect.py", "_state_user", "objects", "user_model", "record.user_id"):
        "StateRecord is a frozen OAuth payload, not a Django model with a user FK.",
    (
        "addons/angee/workflows/engine.py", "advance_dispatch", "objects",
        "apps.get_model('workflows', 'WorkflowRun')", "preflight.envelope.target_id",
    ): "WorkflowDispatchEnvelope carries a frozen dispatch identifier, not a model FK.",
    (
        "addons/angee/workflows/engine.py", "schedule_result", "objects",
        "attempt_model", "finalization.retry_intent.attempt_id",
    ): "RetryIntent is a frozen attempt identifier without a field-bearing model instance.",
    (
        "addons/angee/workflows/engine.py", "schedule_result", "objects",
        "apps.get_model('workflows', 'Decision')", "intent.decision_id",
    ): "DecisionTimerIntent captures an identifier rather than a Django relation.",
    (
        "addons/angee/workflows/engine.py", "_expand_retained_map_step", "objects",
        "apps.get_model('workflows', 'Step')", "plan.target_id",
    ): "MapExpansionPlan is a frozen definition result, not the owner of a target FK.",
}


def test_fk_reloads_use_related_on() -> None:
    """Keep bare FK reloads at their owner; exceptions preserve scoped or ID-only policy."""

    violations: list[str] = []
    for root in (PROJECT_ROOT / "angee", PROJECT_ROOT / "addons/angee"):
        for site in _fk_reloads(root):
            relative = site.path.relative_to(PROJECT_ROOT).as_posix()
            if relative == "angee/base/db.py":
                continue
            key = (relative, site.function, site.manager, site.model, site.pk)
            if key not in _FK_RELOAD_EXEMPTIONS:
                violations.append(f"{relative}:{site.line} ({site.manager}, {site.function})")

    assert not violations, (
        "Bare FK reloads must use angee.base.db.related_on(instance, field_name, using=...):\n"
        + "\n".join(violations)
    )
