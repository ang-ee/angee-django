"""Measure physical lines for the workflow L0/L1 file-and-symbol budget map.

Execution owns managers.py except DraftSave and WorkflowManager, tasks.py, and
Definition.ready_nodes plus Definition._edge_live. Definition/validation/bindings
owns the rest of definition.py, bindings.py, DraftSave, and WorkflowManager. Step contracts
own steps.py and context.py. Models own models.py, states.py, and permissions.zed.
Blank lines, comments, docstrings, and decorators count; every mapped file line
belongs to exactly one row. Limits are fixed; exceeding one fails this command.
"""

from __future__ import annotations

import ast
from pathlib import Path


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
    """Print the four disjoint rows and fail if any exceeds its design budget."""

    root = Path(__file__).resolve().parents[2] / "addons" / "angee" / "workflows"
    names = (
        "managers.py", "tasks.py", "definition.py", "bindings.py", "steps.py",
        "context.py", "models.py", "states.py", "permissions.zed",
    )
    sources = {name: (root / name).read_text(encoding="utf-8") for name in names}
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
        ("Step contract, context, built-in steps", 900, {
            "steps.py": whole["steps.py"], "context.py": whole["context.py"],
        }),
        ("Models, constraints, permissions", 900, {
            "models.py": whole["models.py"], "states.py": whole["states.py"],
            "permissions.zed": whole["permissions.zed"],
        }),
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
    return int(exceeded)


if __name__ == "__main__":
    raise SystemExit(main())
