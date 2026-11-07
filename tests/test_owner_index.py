"""The owner index names only symbols that exist where it says they are.

Static: run with ``-p no:django --noconftest`` like ``test_agent_methodology.py``.
Every Owner cell cites a ```Symbol` path``; the cited file declares the symbol: a
Python ``def``, ``class`` or assignment target at any depth, or an exported
declaration of a TypeScript or ES module. Every public class a foundation
``models``/``fields`` module declares and every ``angee.*`` source distribution has
a row, including explicit libraries. No skill, capability guide or reference file
cites a line, as a path, a shorthand or in prose: only ``docs/`` references keep one.
"""

from __future__ import annotations

import ast
import re
from functools import cache
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
SKILL_DOCUMENTS = sorted([*ROOT.glob(".agents/skills/**/*.md"), *ROOT.glob("src/angee/*/skills/**/*.md")])
CITATION = re.compile(r"`(\w+)` ((?:src|packages|examples)/[\w./-]+\.\w+)")
LOCATION = re.compile(r"(?<![\w/])((?:src|packages|examples)/[\w./-]+\.\w+)")
LINE_REFERENCE = re.compile(
    r"(?<![\w/.-])(?!(?:\.\./)*docs/)(?:[\w.{}*-]+/)*[\w.{}*-]*\w\.[a-z]+:\d+"  # path:line, path:line-line
    r"|\bREADME:\d+"  # a README line
    r"|\b(?:guidelines|glossary|stack|checks|composer|frontend|backend|appearance):\d+"  # a guide line in shorthand
    r"|`:\d+|(?<=[\s(]):\d+(?=[\s),;.-]|$)"  # a bare :line continuing an earlier path
    r"|\bline \d+\b"  # a line cited in prose, beside the anchor that already names the rule
)
EXPORT = re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:const|function|class|type|interface)\s+(\w+)", re.M)


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


def citations() -> list[tuple[str, str]]:
    return [citation for cell in owner_cells() for citation in CITATION.findall(cell)]


@cache
def declarations(source: Path) -> frozenset[str]:
    """Return the names ``source`` declares, so a citation survives moves within its file."""
    text = source.read_text()
    if source.suffix != ".py":
        return frozenset(EXPORT.findall(text))
    names = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(
                name.id
                for target in targets
                for name in ast.walk(target)
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
            )
    return frozenset(names)


def foundation_classes() -> list[tuple[str, str]]:
    """Return ``(class, declaring module)`` for every public top-level class of the foundation modules."""
    return [
        (node.name, module.relative_to(ROOT).as_posix())
        for module in FOUNDATION_MODULES
        for node in ast.parse(module.read_text()).body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    ]


def package_folders() -> list[str]:
    """Cover authored packages without re-deriving their generated entry points."""
    return sorted(package.parent.relative_to(ROOT).as_posix() for package in ADDONS.glob("*/pyproject.toml"))


def test_every_owner_cell_cites_a_symbol() -> None:
    cells = owner_cells()
    assert cells, "the index has no owner rows"
    for cell in cells:
        assert CITATION.search(cell), f"an Owner cell cites no `symbol` path: {cell}"


@pytest.mark.parametrize("document", SKILL_DOCUMENTS, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_skills_cite_no_lines(document: Path) -> None:
    """Skills cite a symbol, heading or file, so a moved line never stales them; ``docs/`` lines are exempt."""
    lines = LINE_REFERENCE.findall(document.read_text())
    assert not lines, f"cite a symbol or heading instead of {lines}"


@pytest.mark.parametrize(("symbol", "path"), citations(), ids=lambda value: str(value))
def test_cited_file_declares_the_symbol(symbol: str, path: str) -> None:
    source = ROOT / path
    assert source.is_file(), path
    assert symbol in declarations(source), f"{path} does not declare {symbol}"


@pytest.mark.parametrize("path", sorted({path for cell in owner_cells() for path in LOCATION.findall(cell)}))
def test_every_cited_owner_path_exists(path: str) -> None:
    assert (ROOT / path).is_file(), path


@pytest.mark.parametrize(("symbol", "module"), foundation_classes(), ids=lambda value: value.rpartition("/")[2])
def test_every_public_foundation_class_has_an_owner_row(symbol: str, module: str) -> None:
    """A public class an ``angee.base.models``/``angee.base.fields`` module declares is cited at that module."""
    assert (symbol, module) in set(citations()), f"no Owner cell cites `{symbol}` {module}"


@pytest.mark.parametrize("folder", package_folders())
def test_every_package_has_an_owner_row(folder: str) -> None:
    """Each distribution has an owner definition within its source folder."""
    assert any(f"{folder}/" in cell for cell in owner_cells()), f"no Owner cell cites {folder}"
