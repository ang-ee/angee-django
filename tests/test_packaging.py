"""Native packaging identity, closure and release-cohort contracts."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
LIBRARIES = {"angee-integrate-iphone"}


def release():
    spec = importlib.util.spec_from_file_location("packaging_release", ROOT / "scripts/release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def members():
    return release().projects(ROOT)[1:]


def test_native_addon_members_reach_base_and_explicit_library_stays_outside_group():
    projects = {
        project["name"]: project
        for path in members()
        if (project := tomllib.loads((path / "pyproject.toml").read_text())["project"])
    }
    assert ROOT / "tests/blankchoices" in members()
    assert "django-angee" not in projects
    assert "angee.web" in projects["angee-web"]["entry-points"]["angee.addons"]
    for path in members():
        data = tomllib.loads((path / "pyproject.toml").read_text())
        project = data["project"]
        name = project["name"]
        if name == "hatch-angee":
            assert path == ROOT / "tools/hatch-angee"
            assert project["version"].startswith("1.")
            continue
        if name in LIBRARIES:
            assert project["requires-python"] == ">=3.14" and project["license"]
            assert not project.get("entry-points", {}).get("angee.addons")
            assert not data.get("tool", {}).get("angee")
            continue
        points = project["entry-points"]["angee.addons"]
        assert len(points) == 1
        app = next(iter(points))
        assert name == app.replace(".", "-").replace("_", "-")
        assert data["build-system"]["build-backend"] == "hatch_angee"
        if name == "angee-base":
            assert project["requires-python"] == ">=3.14" and project["license"]
        else:
            assert "requires-python" not in project and "license" not in project
        pending = [name]
        visited = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(
                parsed.name
                for raw in projects[current].get("dependencies", ())
                if (parsed := Requirement(raw)).name in projects
                and (parsed.marker is None or parsed.marker.evaluate({"extra": ""}))
            )
        assert "angee-base" in visited, app
        facts = data.get("tool", {}).get("angee", {})
        assert "category" not in facts and not {"base", "mcp", "web"}.intersection(facts)


def test_meta_exact_pins_and_native_sources_match_actual_members():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    production = {path for path in members() if path.parent == ROOT / "addons/angee"}
    requirements = {parsed.name: parsed for raw in data["project"]["dependencies"] if (parsed := Requirement(raw))}
    assert len(requirements) == len(production)
    for path in production:
        project = tomllib.loads((path / "pyproject.toml").read_text())["project"]
        assert str(requirements[project["name"]].specifier) == f"=={project['version']}"
    projects = {tomllib.loads((path / "pyproject.toml").read_text())["project"]["name"] for path in members()}
    sources = data["tool"]["uv"]["sources"]
    assert {name for name, source in sources.items() if source.get("workspace")} == projects
    assert "addons" not in data.get("dependency-groups", {})
    assert not list((ROOT / "addons").rglob(".gitignore"))
    assert not list((ROOT / "addons").rglob("addon.toml"))


@pytest.mark.parametrize(("kind", "expected"), [("patch", "3.0.1"), ("major", "4.0.0")])
def test_native_release_bumps_only_cohort_and_updates_all_consumers(tmp_path, kind, expected):
    paths = {
        "": ("angee", "3.0.0", ["angee-base==3.0.0", "angee-web==3.0.0"]),
        "addons/angee/base": ("angee-base", "3.0.0", []),
        "addons/angee/web": ("angee-web", "3.0.0", ["angee-base>=3,<4"]),
        "tools/hatch-angee": ("hatch-angee", "1.0.1", []),
        "examples/addons/example/notes": ("example-notes", "0.9.0", ["angee-base>=3,<4"]),
        "tests/blankchoices": ("tests-blankchoices", "0.7.0", ["angee-web==3.0.0"]),
    }
    for relative, (name, version, requirements) in paths.items():
        directory = tmp_path / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "pyproject.toml").write_text(
            f'[project]\nname={name!r}\nversion={version!r}\nrequires-python=">=3.14"\ndependencies={requirements!r}\n'
            "[tool.uv]\npackage=false\n"
        )
    target = tmp_path / "pyproject.toml"
    with target.open("a") as stream:
        stream.write("""
[tool.uv.workspace]
members=["addons/angee/*","tools/hatch-angee","examples/addons/example/*","tests/blankchoices"]
[tool.uv.sources]
angee-base={workspace=true}
angee-web={workspace=true}
[dependency-groups]
dev=["example-notes==0.9.0","tests-blankchoices==0.7.0"]
""")
        stream.write(
            "[tool.uv.sources.example-notes]\nworkspace=true\n[tool.uv.sources.tests-blankchoices]\nworkspace=true\n"
        )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/release.py"), "--root", str(tmp_path), "--bump", kind],
        text=True,
        capture_output=True,
        env={**os.environ, "UV_OFFLINE": "1", "UV_CACHE_DIR": str(tmp_path / "cache")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for relative, (name, version, _) in paths.items():
        project = tomllib.loads((tmp_path / relative / "pyproject.toml").read_text())["project"]
        is_cohort = not relative or relative.startswith("addons/angee/")
        assert project["version"] == (expected if is_cohort else version)
        for raw in project.get("dependencies", []):
            parsed = Requirement(raw)
            if parsed.name in {"angee-base", "angee-web"}:
                assert expected in parsed.specifier
    assert (tmp_path / "uv.lock").is_file()
    assert not (tmp_path / ".venv").exists()
