"""Measure workflow delivery budgets, including the independent decisions addon.

Execution owns managers.py except DraftSave and WorkflowManager, tasks.py, and
Definition.ready_nodes plus Definition._edge_live. Definition/validation/bindings
owns the rest of definition.py, bindings.py, DraftSave, and WorkflowManager. Step contracts
own steps.py, context.py, reviews.py, maps.py, and awaits.py. Models own models.py,
fields.py, states.py, and permissions.zed.
Blank lines, comments, docstrings, and decorators count; every source file line
belongs to exactly one row. The README and addon declaration are named non-code
exceptions. Bytecode caches and node_modules are generated artifacts. The web subtree has one
separate budget for all its sources. An unknown backend file or an exceeded
fixed budget fails this command.
Decisions reports Python, permission, and manifest sources, excluding its testing app.
Its budget comparison is informational;
required public contract docstrings remain included in the reported physical count.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "addons" / "angee" / "workflows"
"""Source addon whose complete inventory must agree with the budget map."""

NON_CODE_FILES = frozenset({"README.md", "addon.toml"})
"""Named declarations and prose outside the physical code budgets."""

WHOLE_FILE_ROWS = (
    ("Step contract, context, built-in steps", 900, ("steps.py", "context.py", "reviews.py", "maps.py", "awaits.py")),
    ("Models, constraints, permissions", 900, (
        "models.py", "fields.py", "states.py", "permissions.zed", "permissions.extends.zed",
    )),
    ("Triggers and sources", 500, ("triggers.py", "sources.py")),
    ("GraphQL schema", 700, ("schema.py",)),
    ("Resources, autoconfig, settings", 300, ("__init__.py", "apps.py", "resources.py", "autoconfig.py")),
    ("Testing harness", 500, (
        "testing/__init__.py", "testing/apps.py", "testing/models.py", "testing/fixtures.py", "testing/drivers.py",
    )),
)
"""Named whole-file owners, including the design's optional later-phase sources."""


def symbol_lines(source: str, symbol: str) -> set[int]:
    """Locate a declared class or method, including its decorators."""

    scope = ast.parse(source).body
    for name in symbol.split("."):
        matches = [
            node for node in scope
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected one declaration for {symbol!r}; found {len(matches)} at {name!r}.")
        node = matches[0]
        scope = node.body
    assert node.end_lineno is not None
    start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    return set(range(start, node.end_lineno + 1))


def main() -> int:
    """Print all rows, rejecting unmapped workflow files and exceeded workflow limits."""

    names = {"managers.py", "tasks.py", "definition.py", "bindings.py"}
    names.update(name for _, _, files in WHOLE_FILE_ROWS for name in files)
    inventory = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and {"__pycache__", "node_modules"}.isdisjoint(path.relative_to(ROOT).parts)
    }
    web_files = {name for name in inventory if name.startswith("web/")}
    backend_inventory = inventory - web_files
    if unmapped := backend_inventory - names - NON_CODE_FILES:
        raise ValueError(f"Unmapped workflow files: {', '.join(sorted(unmapped))}.")
    sources = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in sorted((names & backend_inventory) | web_files)
    }
    whole = {name: set(range(1, len(source.splitlines()) + 1)) for name, source in sources.items()}
    authoring = (
        symbol_lines(sources["managers.py"], "DraftSave")
        | symbol_lines(sources["managers.py"], "WorkflowManager")
    )
    planning = (
        symbol_lines(sources["definition.py"], "Definition.ready_nodes")
        | symbol_lines(sources["definition.py"], "Definition._edge_live")
    )
    rows = (
        ("Execution", 1500, {
            "managers.py": whole["managers.py"] - authoring,
            "tasks.py": whole["tasks.py"],
            "definition.py": planning,
        }),
        ("Definition, validation, bindings", 1000, {
            "definition.py": whole["definition.py"] - planning,
            "bindings.py": whole["bindings.py"],
            "managers.py": authoring,
        }),
        *((name, budget, {file: whole[file] for file in files if file in whole})
          for name, budget, files in WHOLE_FILE_ROWS),
        ("Workflows web", 2200, {file: whole[file] for file in sorted(web_files)}),
    )
    for name, expected in whole.items():
        covered: set[int] = set()
        for _, _, files in rows:
            selected = files.get(name, set())
            if covered & selected:
                raise ValueError(f"Overlapping row coverage in {name}.")
            covered |= selected
        if covered != expected:
            raise ValueError(f"Incomplete row coverage in {name}.")

    print("| Area | Physical lines | Budget | Remaining |")
    print("|---|---:|---:|---:|")
    exceeded = False
    for name, budget, files in rows:
        count = sum(len(lines) for lines in files.values())
        print(f"| {name} | {count} | {budget} | {budget - count} |")
        exceeded |= count > budget
    decision_root = ROOT.parent / "decisions"
    decision_count = sum(
        len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(decision_root.rglob("*"))
        if path.suffix in {".py", ".zed", ".toml"} and "testing" not in path.relative_to(decision_root).parts
    )
    print(f"| Decisions backend | {decision_count} | 1400 | {1400 - decision_count} |")
    return int(exceeded)


if __name__ == "__main__":
    raise SystemExit(main())
