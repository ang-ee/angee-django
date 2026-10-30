"""The owner index names only symbols that exist where it says they are.

Static: run with ``-p no:django --noconftest`` like ``test_agent_methodology.py``.
Every Owner cell cites a ``symbol path:line``, every ``path:line`` in it is such a
citation, and the cited line (or a decorator line above it) declares the symbol.
Every public class a foundation ``models``/``fields`` module declares and every
``angee.*`` addon has a row.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / ".agents/skills/angee-design/references/owners.md"
ADDONS = ROOT / "src/angee"
FOUNDATION_MODULES = sorted(
    module
    for area in ("models", "fields")
    for module in (ADDONS / "base" / area).glob("*.py")
    if module.name != "__init__.py"
)
CITATION = re.compile(r"`(\w+)` ((?:src|packages|examples)/[\w./-]+):(\d+)")
LOCATION = re.compile(r"(?<![\w/])((?:src|packages|examples)/[\w./-]+\.\w+)(?::(\d+))?")


def owner_cells() -> list[str]:
    """Return the Owner column of every data row in the index tables."""
    cells = []
    for line in INDEX.read_text().splitlines():
        if not line.startswith("|") or set(line.strip("| ")) <= {"-", "|", " "}:
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if columns[0] != "Concern keywords":
            cells.append(columns[1])
    return cells


def citations() -> list[tuple[str, str, int]]:
    return [(symbol, path, int(line)) for cell in owner_cells() for symbol, path, line in CITATION.findall(cell)]


def foundation_classes() -> list[tuple[str, str]]:
    """Return ``(class, declaring module)`` for every public top-level class of the foundation modules."""
    return [
        (node.name, module.relative_to(ROOT).as_posix())
        for module in FOUNDATION_MODULES
        for node in ast.parse(module.read_text()).body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    ]


def addons() -> list[tuple[str, str]]:
    """Return native addon entry-point names and folders, excluding library packages."""
    return sorted(
        (name, package.parent.relative_to(ROOT).as_posix())
        for package in ADDONS.glob("*/pyproject.toml")
        for name in tomllib.loads(package.read_text())["project"].get("entry-points", {}).get("angee.addons", {})
    )


def declares(line: str, symbol: str) -> bool:
    keyword = r"(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:class|def|function|const|let)\s+"
    return re.search(rf"^\s*(?:{keyword}{symbol}\b|{symbol}\s*[:=])", line) is not None


def test_every_owner_cell_cites_a_definition() -> None:
    cells = owner_cells()
    assert cells, "the index has no owner rows"
    for cell in cells:
        assert CITATION.search(cell), f"an Owner cell cites no `symbol` path:line: {cell}"


def test_every_path_line_in_an_owner_cell_is_a_parsed_citation() -> None:
    for cell in owner_cells():
        cited = {(path, line) for _, path, line in CITATION.findall(cell)}
        for path, line in LOCATION.findall(cell):
            assert not line or (path, line) in cited, f"{path}:{line} is not a `symbol` path:line citation: {cell}"


@pytest.mark.parametrize(("symbol", "path", "line"), citations(), ids=lambda value: str(value))
def test_cited_line_declares_the_symbol(symbol: str, path: str, line: int) -> None:
    source = ROOT / path
    assert source.is_file(), path
    lines = source.read_text().splitlines()
    assert line <= len(lines), f"{path} has no line {line}"
    index = line - 1
    while index < len(lines) and lines[index].lstrip().startswith("@"):
        index += 1
    assert index < len(lines) and declares(lines[index], symbol), f"{path}:{line} does not declare {symbol}"


@pytest.mark.parametrize("path", sorted({path for cell in owner_cells() for path, _ in LOCATION.findall(cell)}))
def test_every_cited_owner_path_exists(path: str) -> None:
    assert (ROOT / path).is_file(), path


@pytest.mark.parametrize(("symbol", "module"), foundation_classes(), ids=lambda value: value.rpartition("/")[2])
def test_every_public_foundation_class_has_an_owner_row(symbol: str, module: str) -> None:
    """A public class an ``angee.base.models``/``angee.base.fields`` module declares is cited at that module."""
    cited = {(cited_symbol, path) for cited_symbol, path, _ in citations()}
    assert (symbol, module) in cited, f"no Owner cell cites `{symbol}` {module}"


@pytest.mark.parametrize(("name", "folder"), addons(), ids=lambda value: value if value.startswith("angee.") else "")
def test_every_addon_has_an_owner_row(name: str, folder: str) -> None:
    """An addon appears by its declared name or by a path inside its folder in an Owner cell."""
    named = re.compile(rf"{re.escape(name)}(?!\w)")
    assert any(named.search(cell) or f"{folder}/" in cell for cell in owner_cells()), f"no Owner cell names {name}"
