"""Read production Python references for the system mail caller guards."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@cache
def caller_sources() -> Mapping[Path, ast.Module]:
    """Read source modules outside the addons' packaged tests."""

    return {
        path.relative_to(ROOT): ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for top in ("src", "examples") for path in sorted((ROOT / top).rglob("*.py"))
        if "tests" not in path.relative_to(ROOT).parts
    }


def reference_lines(tree: ast.Module, name: str) -> list[int]:
    """List references to the writer name, excluding prose and its own definition."""

    return sorted(
        node.lineno for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == name)
        or (isinstance(node, ast.Name) and node.id == name)
        or (isinstance(node, ast.Constant) and node.value == name)
        or (isinstance(node, ast.alias) and name in (node.name, node.asname))
    )
