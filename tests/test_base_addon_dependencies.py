"""Native collection membership and foundation library dependency ownership."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from typing import Any

from django_compose.addons import active_requirements
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _framework_projects() -> dict[str, dict[str, Any]]:
    """Read declared project facts from the actual native framework workspace members."""

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    members = {
        path.resolve()
        for pattern in project["tool"]["uv"]["workspace"]["members"]
        for path in PROJECT_ROOT.glob(pattern)
        if path.is_dir() and (path / "pyproject.toml").is_file()
        and path.is_relative_to(PROJECT_ROOT / "addons/angee")
    }
    assert members, "the framework collection must contain actual native package members"
    result = {}
    for member in sorted(members):
        facts = tomllib.loads((member / "pyproject.toml").read_text())["project"]
        name = canonicalize_name(facts["name"])
        assert name not in result, f"duplicate framework distribution: {name}"
        version = facts["version"]
        assert isinstance(version, str), f"framework version must be explicit: {name}"
        result[name] = facts
    assert "angee-base" in result
    return result


def test_collection_requires_every_framework_workspace_distribution() -> None:
    """Installed collection METADATA includes each declared member at its cohort version."""

    expected = {name: f"=={project['version']}" for name, project in _framework_projects().items()}
    requirements = active_requirements(metadata.requires("angee") or ())

    assert len(requirements) == len(expected)
    assert {canonicalize_name(item.name): str(item.specifier) for item in requirements} == expected


def _exact_foundation_pins(projects: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Inspect authored runtime declarations; platform and extra activation are separate concerns."""

    foundation = {
        canonicalize_name(Requirement(value).name) for value in projects["angee-base"]["dependencies"]
    }
    assert foundation, "foundation runtime requirements must be declared"
    return [
        f"{name}: {item}"
        for name, project in sorted(projects.items()) if name != "angee-base"
        for value in project.get("dependencies", ())
        for item in (Requirement(value),)
        if canonicalize_name(item.name) in foundation
        and any(spec.operator in ("==", "===") for spec in item.specifier)
    ]


def test_capabilities_leave_exact_foundation_library_pins_to_base() -> None:
    """Capabilities never add competing exact foundation pins, including on other platforms."""

    assert _exact_foundation_pins(_framework_projects()) == []


def test_foundation_pin_guard_includes_foreign_platform_runtime_declarations() -> None:
    projects = {
        "angee-base": {"dependencies": ["django>=6.1.2,<6.2"]},
        "angee-probe": {"dependencies": [f'django==6.1.2; sys_platform != "{sys.platform}"']},
    }
    conflicts = _exact_foundation_pins(projects)
    assert len(conflicts) == 1
    assert "angee-probe: django==6.1.2" in conflicts[0]
