"""Contract tests for the documented Angee stack."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "docs" / "stack.md"
SEPARATOR_ROW = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
PICK_TOKEN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


def normalize(name: str) -> str:
    return canonicalize_name(name)


def requirement_name(requirement: str) -> str:
    """The normalized distribution name a requirement string declares."""
    return canonicalize_name(Requirement(requirement).name)


def declared_requirements() -> dict[str, set[str]]:
    """Map each declared Python dependency to the manifests that declare it."""
    owners: dict[str, set[str]] = {}
    projects = [ROOT / "core/pyproject.toml", *sorted(ROOT.glob("addons/*/*/pyproject.toml"))]
    internal = {tomllib.loads(path.read_text())["project"]["name"] for path in projects}
    groups = {}
    for path in projects:
        project = tomllib.loads(path.read_text())["project"]
        owner = path.relative_to(ROOT).as_posix()
        groups[owner] = [raw for raw in project.get("dependencies", []) if Requirement(raw).name not in internal]
        groups.update({f"{owner}[{extra}]": reqs for extra, reqs in project.get("optional-dependencies", {}).items()})
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

    data = tomllib.loads((ROOT / "core/pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(data["project"]["dependencies"])

    jobs = tomllib.loads((ROOT / "addons/angee/jobs/pyproject.toml").read_text())["project"]["dependencies"]
    graphql = tomllib.loads((ROOT / "addons/angee/graphql/pyproject.toml").read_text())["project"]["dependencies"]
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
