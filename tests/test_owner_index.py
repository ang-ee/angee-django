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

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / ".agents/skills/angee-design/references/owners.md"
ADDONS = ROOT / "src/angee"
FOUNDATION_MODULES = sorted(
    module
    for area in ("models", "fields")
    for module in (ADDONS / "base" / area).glob("*.py")
    if module.name != "__init__.py"
)
CITATION = re.compile(r"`(\w+)` ((?:src|packages|examples)/[\w./-]+\.\w+)")
LOCATION = re.compile(r"(?<![\w/])((?:src|packages|examples)/[\w./-]+\.\w+)")
SKILL_DOCUMENTS = sorted([*ROOT.glob(".agents/skills/**/*.md"), *ROOT.glob("src/angee/*/skills/**/*.md")])
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


def test_every_owner_cell_cites_symbols_its_files_declare() -> None:
    cells = owner_cells()
    assert cells, "the index has no owner rows"
    problems = [f"cites no `symbol` path: {cell}" for cell in cells if not CITATION.search(cell)]
    problems += [f"missing {path}" for path in sorted({p for cell in cells for p in LOCATION.findall(cell)})
                 if not (ROOT / path).is_file()]
    problems += [f"{path} does not declare {symbol}" for symbol, path in citations()
                 if (ROOT / path).is_file() and symbol not in declarations(ROOT / path)]
    assert not problems, "\n".join(problems)


def test_every_foundation_class_and_package_has_an_owner_row() -> None:
    """A public foundation ``models``/``fields`` class is cited at its module; each distribution has a row."""
    cells, cited = owner_cells(), set(citations())
    missing = [f"`{symbol}` {module}" for symbol, module in foundation_classes() if (symbol, module) not in cited]
    missing += [folder for folder in package_folders() if not any(f"{folder}/" in cell for cell in cells)]
    assert not missing, "no Owner cell cites:\n" + "\n".join(missing)


def test_skills_cite_no_lines() -> None:
    """Skills cite a symbol, heading or file, so a moved line never stales them; ``docs/`` lines are exempt."""
    cited = {
        document.relative_to(ROOT).as_posix(): lines
        for document in SKILL_DOCUMENTS
        if (lines := LINE_REFERENCE.findall(document.read_text()))
    }
    assert not cited, f"cite a symbol or heading instead of {cited}"
