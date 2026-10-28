"""Contract tests for the documented Angee stack."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

try:
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover - the dev venv ships packaging; the regex covers a bare interpreter
    Requirement = None

ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "docs" / "stack.md"
REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
SEPARATOR_ROW = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
PICK_TOKEN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


def normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def requirement_name(requirement: str) -> str:
    """The normalized distribution name a requirement string declares."""
    if Requirement is not None:
        return normalize(Requirement(requirement).name)
    match = REQUIREMENT_NAME.match(requirement)
    assert match, f"unparseable requirement: {requirement!r}"
    return normalize(match.group(1))


def declared_requirements() -> dict[str, set[str]]:
    """Map each declared Python dependency to the manifests that declare it."""
    owners: dict[str, set[str]] = {}
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    groups = {"pyproject.toml": project.get("dependencies", [])}
    groups |= {f"pyproject.toml[{extra}]": reqs for extra, reqs in project.get("optional-dependencies", {}).items()}
    for manifest in sorted(ROOT.glob("addons/*/*/addon.toml")):
        addon = tomllib.loads(manifest.read_text(encoding="utf-8"))["addon"]
        groups[manifest.relative_to(ROOT).as_posix()] = addon.get("dependencies", [])
    for owner, requirements in groups.items():
        for requirement in requirements:
            owners.setdefault(requirement_name(requirement), set()).add(owner)
    return owners


def test_stack_names_celery_not_procrastinate() -> None:
    """The locked task engine is Celery over Redis, not Procrastinate."""

    text = (ROOT / "docs" / "stack.md").read_text(encoding="utf-8")

    assert "Celery + Redis" in text
    assert "| Procrastinate |" not in text


def test_task_and_web_dependencies_belong_to_their_addons() -> None:
    """Jobs owns task transport; core and GraphQL declare native Channels usage."""

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(data["project"]["dependencies"])

    jobs = tomllib.loads((ROOT / "addons/angee/jobs/addon.toml").read_text())["addon"]["dependencies"]
    graphql = tomllib.loads((ROOT / "addons/angee/graphql/addon.toml").read_text())["addon"]["dependencies"]
    assert any(dependency.startswith("celery[redis]") for dependency in jobs)
    assert any(dependency.startswith("channels>=") for dependency in graphql)
    assert not any(dependency.startswith("celery") for dependency in dependencies)
    assert any(dependency.startswith("channels>=") for dependency in dependencies)
    assert not any(dependency.startswith("channels-redis") for dependency in dependencies)
    assert not any("procrastinate" in dependency for dependency in dependencies)


def stack_picks() -> set[str]:
    """Normalized name tokens of the Pick cell of every locked ``docs/stack.md`` table row."""
    locked, heading, _ = STACK.read_text(encoding="utf-8").partition("\n## Proposed, Not Locked\n")
    assert heading, "docs/stack.md no longer separates proposed picks from locked ones"
    lines = locked.splitlines()
    picks: set[str] = set()
    for index, line in enumerate(lines):
        header = index + 1 < len(lines) and SEPARATOR_ROW.match(lines[index + 1])
        if line.startswith("|") and not header and not SEPARATOR_ROW.match(line):
            picks.update(PICK_TOKEN.findall(normalize(line.strip().strip("|").split("|")[0])))
    return picks


def test_every_declared_dependency_has_a_stack_row() -> None:
    """Every core, extra and addon Python dependency is the Pick of a locked ``docs/stack.md`` row."""

    picks = stack_picks()
    missing = {name: sorted(owners) for name, owners in declared_requirements().items() if name not in picks}

    assert not missing, f"dependencies without a docs/stack.md row: {missing}"
