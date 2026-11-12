"""Tests patch boundaries and owner fakes, never internals.

A patch of an underscore-prefixed name, of ``type(<model, manager or queryset>)``
(directly or through a local alias), or of the workflow runner's module reaches
past the owner's contract; use the owner's public seam, its fake in the owning
addon's ``testing`` package, or an outcome assertion instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).parent
RUNNER_MODULES = ("angee.workflows.runner",)
ALLOWED = {
    # The ACP adapter's sync-to-async persistence boundary: one test fails a
    # follower read there, one measures each follower read's queries per thread.
    ("test_agents_acp.py", "SessionAgent", "_db"),
    # The adopt race: another agent records the workspace between the conflict
    # check and the record; nothing public can interleave that window on SQLite.
    ("test_agents_graphql.py", "type(agent)", "conflicting_instance_blocker"),
}


def _patches(tree: ast.Module) -> list[tuple[int, str, str]]:
    """Return ``(line, dotted target, attribute)`` for every patch call."""

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args or not isinstance(node.func, ast.Attribute | ast.Name):
            continue
        name = ast.unparse(node.func)
        first = node.args[0]
        second = node.args[1] if len(node.args) > 1 else None
        if name in {"monkeypatch.setattr", "monkeypatch.delattr", "patch.object", "mock.patch.object"}:
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and "." in first.value:
                dotted, _, attribute = first.value.rpartition(".")
                found.append((node.lineno, dotted, attribute))
            elif isinstance(second, ast.Constant) and isinstance(second.value, str):
                found.append((node.lineno, ast.unparse(first), second.value))
        elif name in {"patch", "mock.patch"} and isinstance(first, ast.Constant) and isinstance(first.value, str):
            dotted, _, attribute = first.value.rpartition(".")
            found.append((node.lineno, dotted, attribute))
    return found


def _runner_aliases(tree: ast.Module) -> set[str]:
    """Return local names bound to the workflow runner's module."""

    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if f"{node.module}.{alias.name}" in RUNNER_MODULES:
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            aliases.update(alias.asname or alias.name for alias in node.names if alias.name in RUNNER_MODULES)
    return aliases


def _type_aliases(tree: ast.Module) -> list[tuple[int, int, str]]:
    """Return ``(first line, last line, name)`` for each function-local ``name = type(...)``."""

    aliases = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "type"
                and len(node.value.args) == 1
            ):
                aliases.extend(
                    (function.lineno, function.end_lineno or function.lineno, target.id)
                    for target in node.targets
                    if isinstance(target, ast.Name)
                )
    return aliases


def test_tests_patch_no_internals() -> None:
    offences = []
    for path in sorted(TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        runner, typed = _runner_aliases(tree), _type_aliases(tree)
        for line, target, attribute in _patches(tree):
            root = target.split(".", 1)[0]
            internal = attribute.startswith("_") and not (attribute.startswith("__") and attribute.endswith("__"))
            aliased = any(first <= line <= last and root == name for first, last, name in typed)
            by_type = target.startswith("type(") or aliased
            engine = root in runner or target.startswith(RUNNER_MODULES)
            if (internal or by_type or engine) and (path.name, target, attribute) not in ALLOWED:
                offences.append(f"{path.relative_to(TESTS)}:{line} {target}.{attribute}")
    assert not offences, "Patch the owner's public seam or fake instead:\n" + "\n".join(offences)
