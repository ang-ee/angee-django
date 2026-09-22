"""Guard framework import boundaries and ownership of database routing seams."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest

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


def _routing_drift(tree: ast.Module, *, addon: bool) -> Iterator[tuple[str, int, str]]:
    """Find routing drift syntactically, without importing Django or addon models.

    A write-owner module contains get_write_alias/full_clean_for_write, a Django
    transaction boundary, or an ORM-shaped mutator call or definition: save/save_base/create,
    get_or_create/update_or_create/update/delete/bulk_create/bulk_update (including
    async forms). Defining a save/delete override establishes write ownership
    even if its only call is full_clean. This identifies syntax, not receiver
    types or interprocedural dataflow. Aliased Django imports are resolved; dynamic getattr,
    re-exports and aliases assigned at runtime are outside the check.

    Within that module, every Django atomic/on_commit must supply an explicit
    non-None alias, positionally or by using; opaque **kwargs do not establish it.
    Every addon .full_clean call is a candidate. The one syntactic exemption is
    super().full_clean inside a full_clean override: it implements the native
    validation hook called by full_clean_for_write, rather than initiating a write.
    Form-only modules without write syntax are outside scope; a form validation
    in a mixed write module needs an individually reasoned exemption below.

    Manager/queryset .db reads are forbidden even in pure-read modules. Recognize
    native manager attributes, their fluent calls, get_queryset/system_queryset,
    self in Manager/QuerySet classes or mixins, conventional queryset/manager/qs
    names, and annotated or locally assigned aliases of those expressions.
    Native row/scalar-returning calls terminate that recognition. Other .db
    attributes (instance._state.db, version.db, environment.db) are not ORM
    collection expressions. This is not cross-module or dynamic type inference.

    Inline deferred refresh preambles belong to refresh_deferred. Recognize a
    same-receiver refresh with explicit fields directly inside a get_deferred_fields
    guard, including membership, walrus and locally assigned field sets/intersections.
    Unrestricted refreshes reload loaded columns and retain Django's native owner.
    Arbitrary dataflow and indirect refresh calls are outside this syntactic check.
    """

    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for name in node.names:
                imports[name.asname or name.name] = f"{node.module}.{name.name}"
        elif isinstance(node, ast.Import):
            for name in node.names:
                imports[name.asname or name.name.split(".")[0]] = name.name if name.asname else name.name.split(".")[0]

    def name_of(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return imports.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{name_of(node.value)}.{node.attr}"
        return ""

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    boundaries = {"django.db.transaction.atomic", "django.db.transaction.on_commit"}
    mutators = {
        "save", "save_base", "create", "get_or_create", "update_or_create", "update", "delete",
        "bulk_create", "bulk_update",
    }
    mutators |= {f"a{name}" for name in mutators}
    owners = [
        node for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    functions = [node for node in owners if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def scope_of(node: ast.AST) -> ast.AST:
        return max(
            (owner for owner in functions if owner.lineno <= node.lineno <= (owner.end_lineno or owner.lineno)),
            key=lambda owner: owner.lineno,
            default=tree,
        )

    def collection_type(node: ast.AST) -> bool:
        return any(
            isinstance(part, (ast.Name, ast.Attribute))
            and (name_of(part).endswith("Manager") or "QuerySet" in name_of(part).rsplit(".", 1)[-1])
            for part in ast.walk(node)
        )

    collection_names: dict[ast.AST, set[str]] = {scope: set() for scope in [tree, *functions]}
    for function in functions:
        for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]:
            if arg.annotation is not None and collection_type(arg.annotation):
                collection_names[function].add(arg.arg)

    def collection_expression(node: ast.AST, scope: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            if node.id in {"queryset", "manager", "qs"} | collection_names[scope] | collection_names[tree]:
                return True
            if node.id == "self":
                classes = [
                    owner for owner in owners if isinstance(owner, ast.ClassDef)
                    and owner.lineno <= node.lineno <= (owner.end_lineno or owner.lineno)
                ]
                owner = max(classes, key=lambda owner: owner.lineno, default=None)
                return owner is not None and (
                    owner.name.endswith("Manager") or "QuerySet" in owner.name
                    or any(collection_type(base) for base in owner.bases)
                )
        elif isinstance(node, ast.Attribute):
            return node.attr in {"objects", "_base_manager", "_default_manager", "queryset", "manager"}
        elif isinstance(node, ast.Call):
            if name_of(node.func) in {"typing.cast", "cast"} and len(node.args) == 2:
                return collection_type(node.args[0]) or collection_expression(node.args[1], scope)
            if isinstance(node.func, ast.Attribute):
                method = node.func.attr
                if method in {"get_queryset", "system_queryset"}:
                    return True
                terminals = {
                    "get", "first", "last", "earliest", "latest", "create", "get_or_create", "update_or_create",
                    "count", "exists", "aggregate", "update", "delete", "bulk_create", "bulk_update", "in_bulk",
                }
                terminals |= {f"a{name}" for name in terminals}
                if method in terminals:
                    return False
                return collection_expression(node.func.value, scope)
            return collection_type(node.func)
        return False

    assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            scope = scope_of(assignment)
            annotated = isinstance(assignment, ast.AnnAssign) and collection_type(assignment.annotation)
            if annotated or assignment.value is not None and collection_expression(assignment.value, scope):
                targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in collection_names[scope]:
                        collection_names[scope].add(target.id)
                        changed = True

    manager_db_reads = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "db" and isinstance(node.ctx, ast.Load)
        and collection_expression(node.value, scope_of(node))
    ]
    bare_decorators = [
        decorator for owner in owners for decorator in owner.decorator_list
        if name_of(decorator) == "django.db.transaction.atomic"
    ]
    write_owner = bool(bare_decorators) or any(owner.name in mutators for owner in functions) or any(
        name_of(call.func) in boundaries
        or name_of(call.func) == "angee.base.db.get_write_alias"
        or isinstance(call.func, ast.Attribute) and call.func.attr in {*mutators, "full_clean_for_write"}
        for call in calls
    )

    deferred_preambles: set[ast.Call] = set()
    for conditional in (node for node in ast.walk(tree) if isinstance(node, ast.If)):
        refreshes = [
            statement.value for statement in conditional.body
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute) and statement.value.func.attr == "refresh_from_db"
        ]
        if not refreshes:
            continue
        conditions = [conditional.test]
        names = {node.id for node in ast.walk(conditional.test) if isinstance(node, ast.Name)}
        local_values: dict[str, ast.expr | None] = {}
        for assignment in sorted(assignments, key=lambda node: node.lineno):
            if assignment.lineno >= conditional.lineno or scope_of(assignment) is not scope_of(conditional):
                continue
            targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in names:
                    local_values[target.id] = assignment.value
        conditions.extend(value for value in local_values.values() if value is not None)
        receivers = {
            ast.dump(node.func.value)
            for condition in conditions for node in ast.walk(condition)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get_deferred_fields"
        }
        for call in refreshes:
            fields = next(
                (keyword.value for keyword in call.keywords if keyword.arg == "fields"),
                call.args[1] if len(call.args) > 1 else None,
            )
            if fields is None or isinstance(fields, ast.Constant) and fields.value is None:
                continue
            if ast.dump(call.func.value) in receivers:
                deferred_preambles.add(call)

    for node in [*calls, *bare_decorators, *manager_db_reads]:
        parents = sorted(
            (owner for owner in owners if owner.lineno <= node.lineno <= (owner.end_lineno or owner.lineno)),
            key=lambda owner: owner.lineno,
        )
        owner_name = ".".join(owner.name for owner in parents)
        if not isinstance(node, ast.Call):
            yield "manager_db" if node in manager_db_reads else "transaction", node.lineno, owner_name
            continue
        called = name_of(node.func)
        if node in deferred_preambles:
            yield "deferred_refresh", node.lineno, owner_name
        elif called in {"django.db.router.db_for_read", "django.db.router.db_for_write"}:
            yield "router", node.lineno, owner_name
        elif write_owner and called in boundaries:
            alias = next((keyword.value for keyword in node.keywords if keyword.arg == "using"), None)
            position = 0 if called.endswith(".atomic") else 1
            if alias is None and len(node.args) > position:
                alias = node.args[position]
            if alias is None or isinstance(alias, ast.Constant) and alias.value is None:
                yield "transaction", node.lineno, owner_name
        elif addon and write_owner and isinstance(node.func, ast.Attribute) and node.func.attr == "full_clean":
            receiver = node.func.value
            native_override = (
                parents and parents[-1].name == "full_clean"
                and isinstance(receiver, ast.Call) and name_of(receiver.func) == "super"
            )
            if not native_override:
                yield "full_clean", node.lineno, owner_name


_ROUTING_EXEMPTIONS = {
    ("addons/angee/platform/permissions.py", "transaction", "reconcile_permission_schema"):
        "Excluded platform lifecycle debt: PackageManagedRecord.target/REBAC cleanup lack alias propagation.",
    ("addons/angee/workflows_agents/sessions.py", "transaction", "start_session"):
        "Excluded agent-session entry debt: agent/provider/session owners still need routing and an entry guard.",
    ("addons/angee/workflows_agents/sessions.py", "transaction", "post_message"):
        "Excluded agent-session entry debt: session/turn owner routing is incomplete.",
    ("addons/angee/workflows_agents/sessions.py", "transaction", "close_session"):
        "Excluded agent-session entry debt: closure/turn cancellation routing is incomplete.",
    ("addons/angee/workflows_agents/steps.py", "transaction", "AgentSessionStepImpl.run"):
        "The workflow entry rejects non-default execution until the excluded agent/session owners support aliases.",
    ("addons/angee/workflows_agents/steps.py", "transaction", "_TurnUpdateSink.flush"):
        "Private persistence callback under AgentSessionStepImpl.run's default-only admission.",
    ("addons/angee/workflows_agents/steps.py", "transaction", "_persist_turn_outcome"):
        "Private outcome persistence under AgentSessionStepImpl.run's default-only admission.",
}


def test_write_owners_keep_routing_at_the_database_owner() -> None:
    """Guard production core/addons; preserve history, test probes and the router owner.

    Migrations/runtime_migrations are released history, tests exercise routers
    and native APIs deliberately, and base/db.py owns alias selection and deferred refresh.
    Those are the only path-wide exemptions. Remaining exceptions are exact
    module/kind/owner entries above; unused entries fail to prevent exemption rot.
    """

    violations: list[str] = []
    used: set[tuple[str, str, str]] = set()
    for root in (PROJECT_ROOT / "angee", PROJECT_ROOT / "addons/angee"):
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            if (
                {"migrations", "runtime_migrations", "tests"}.intersection(path.relative_to(root).parts)
                or path.stem == "tests" or path.stem.startswith("test_")
            ):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for kind, line, owner in _routing_drift(tree, addon=relative.startswith("addons/")):
                if kind in {"router", "manager_db", "deferred_refresh"} and relative == "angee/base/db.py":
                    continue
                key = (relative, kind, owner)
                if key in _ROUTING_EXEMPTIONS:
                    used.add(key)
                else:
                    violations.append(f"{relative}:{line} {owner}: {kind}")
    assert not violations, (
        "Use database alias owners, refresh_deferred, explicit transaction aliases and full_clean_for_write:\n"
        + "\n".join(violations)
    )
    assert used == _ROUTING_EXEMPTIONS.keys(), f"Remove stale routing exemptions: {_ROUTING_EXEMPTIONS.keys() - used}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from django.db import router\nrouter.db_for_write(Model)", ["router"]),
        ("from django.db import router as r\nr.db_for_read(Model)", ["router"]),
        ("from django.db import transaction as tx\nwith tx.atomic(): pass", ["transaction"]),
        ("from django.db.transaction import on_commit as commit\ncommit(callback)", ["transaction"]),
        ("from django.db import transaction\n@transaction.atomic\ndef write(): pass", ["transaction"]),
        ("from django.db import transaction\nwith transaction.atomic(using=None): pass", ["transaction"]),
        ("from django.db import transaction\nwith transaction.atomic(using=alias): pass", []),
        ("from django.db import transaction\ntransaction.on_commit(callback, alias)", []),
        ("row.full_clean()\nrow.save(using=alias)", ["full_clean"]),
        ("row.full_clean_for_write(using=alias)\nrow.save(using=alias)", []),
        ("form.full_clean()", []),
        ("def full_clean(self, **kwargs):\n    super().full_clean(**kwargs)\nrow.save()", []),
        ("def save(self):\n    super().full_clean()", ["full_clean"]),
        ("class Record:\n    def delete(self):\n        self.full_clean()", ["full_clean"]),
        ("class Record:\n    async def asave(self):\n        self.full_clean()", ["full_clean"]),
        ("Model.objects.db", ["manager_db"]),
        ("Model._base_manager.db", ["manager_db"]),
        ("Model._default_manager.db", ["manager_db"]),
        ("Model.objects.using(alias).filter(active=True).db", ["manager_db"]),
        ("queryset.db", ["manager_db"]),
        ("manager.db", ["manager_db"]),
        ("qs.db", ["manager_db"]),
        ("class Owner(models.QuerySet):\n    def read(self):\n        return self.db", ["manager_db"]),
        ("class Owner(models.Manager):\n    def read(self):\n        return self.db", ["manager_db"]),
        ("rows = Model.objects.all()\nselected = rows\nselected.db", ["manager_db"]),
        ("def read(rows: models.QuerySet[Model]):\n    return rows.db", ["manager_db"]),
        ("def read():\n    ledger = Model.objects\n    return ledger.db", ["manager_db"]),
        ("cast(models.QuerySet[Model], value).db", ["manager_db"]),
        ("model.system_queryset().db", ["manager_db"]),
        ("self._state.db", []),
        ("self.env.db", []),
        ("version.db", []),
        ("Version.objects.get(pk=1).db", []),
        ("VersionManager().get(pk=1).db", []),
        (
            "if deferred := row.get_deferred_fields():\n"
            "    row.refresh_from_db(using=alias, fields=deferred)",
            ["deferred_refresh"],
        ),
        (
            "deferred = row.get_deferred_fields()\n"
            "if deferred:\n    row.refresh_from_db(using=alias, fields=deferred)",
            ["deferred_refresh"],
        ),
        (
            "needed = row.get_deferred_fields() & {'state'}\n"
            "if needed:\n    row.refresh_from_db(using=alias, fields=sorted(needed))",
            ["deferred_refresh"],
        ),
        (
            "if field.attname in row.get_deferred_fields():\n"
            "    row.refresh_from_db(using=alias, fields=[field.attname])",
            ["deferred_refresh"],
        ),
        (
            "if deferred := row.get_deferred_fields():\n"
            "    row.refresh_from_db(alias, deferred)",
            ["deferred_refresh"],
        ),
        (
            "if deferred := row.get_deferred_fields():\n"
            "    other.refresh_from_db(using=alias, fields=deferred)",
            [],
        ),
        (
            "def inspect():\n    deferred = row.get_deferred_fields()\n"
            "def reload(deferred):\n    if deferred:\n        row.refresh_from_db(using=alias, fields=deferred)",
            [],
        ),
        (
            "fields = row.get_deferred_fields()\nfields = {'label'}\n"
            "if fields:\n    row.refresh_from_db(using=alias, fields=fields)",
            [],
        ),
        ("if row.get_deferred_fields():\n    row.refresh_from_db(using=alias)", []),
        ("if row.get_deferred_fields():\n    row.refresh_from_db(using=alias, fields=None)", []),
        ("row.refresh_from_db(using=alias, fields=['state'])", []),
        ("refresh_deferred(row, using=alias, fields=['state'])", []),
    ],
)
def test_routing_drift_syntax(source: str, expected: list[str]) -> None:
    """Exercise write definitions, routing aliases and ORM collection false positives."""

    assert [kind for kind, _line, _owner in _routing_drift(ast.parse(source), addon=True)] == expected
