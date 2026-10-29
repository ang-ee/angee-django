"""Static native-package contracts, independent of a composed Django host."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def members():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return {
        path for pattern in data["tool"]["uv"]["workspace"]["members"] for path in ROOT.glob(pattern) if path.is_dir()
    }


def test_native_members_have_one_identity_and_core_in_their_closure():
    projects = {
        data["project"]["name"]: data["project"]
        for path in members()
        if (data := tomllib.loads((path / "pyproject.toml").read_text()))
    }
    assert len(projects) == 73
    for path in members():
        data = tomllib.loads((path / "pyproject.toml").read_text())
        if data["project"]["name"] == "django-angee":
            assert data["project"]["requires-python"] == ">=3.14"
            continue
        project = data["project"]
        points = project["entry-points"]["angee.addons"]
        assert len(points) == 1
        app = next(iter(points))
        assert project["name"] == app.replace(".", "-").replace("_", "-")
        assert data["build-system"]["build-backend"] == "hatch_angee"
        assert "requires-python" not in project and "license" not in project
        pending = [project["name"]]
        visited = set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            pending.extend(
                requirement.name
                for raw in projects[name].get("dependencies", ())
                if (requirement := Requirement(raw)).name in projects
                and (requirement.marker is None or requirement.marker.evaluate({"extra": ""}))
            )
        assert "django-angee" in visited, app


def test_meta_exact_pins_match_production_members():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    requirements = {
        requirement.name: requirement for raw in project["dependencies"] if (requirement := Requirement(raw))
    }
    production = {path for path in members() if path.is_relative_to(ROOT / "addons") or path == ROOT / "core"}
    assert len(requirements) == len(production)
    for path in production:
        member = tomllib.loads((path / "pyproject.toml").read_text())["project"]
        assert str(requirements[member["name"]].specifier) == f"=={member['version']}"
    assert "addons" not in tomllib.loads((ROOT / "pyproject.toml").read_text()).get("dependency-groups", {})
    assert not list((ROOT / "addons").rglob(".gitignore"))
    assert not list(ROOT.rglob("addon.toml"))


def test_release_repin_changes_only_local_meta_and_fixture_versions(tmp_path):
    path = ROOT / "scripts/release.py"
    spec = importlib.util.spec_from_file_location("packaging_release", path)
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    (tmp_path / "pyproject.toml").write_text("""[project]
name = "collection"
version = "2.0.0"
dependencies = ["sample==1.0.0", "upstream>=4"]
[dependency-groups]
dev = ["sample==1.0.0", "pytest>=8"]
""")
    member = tmp_path / "member"
    member.mkdir()
    (member / "pyproject.toml").write_text('[project]\nname="sample"\nversion="2.0.0"\n')
    dependent = tmp_path / "dependent"
    dependent.mkdir()
    (dependent / "pyproject.toml").write_text(
        '[project]\nname="dependent"\nversion="2.0.0"\ndependencies=["sample>=1,<2"]\n'
    )
    release.pin_meta(tmp_path, (member, dependent))
    assert tomllib.loads((dependent / "pyproject.toml").read_text())["project"]["dependencies"] == ["sample<3,>=2"]
    updated = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert updated["project"]["dependencies"] == ["sample==2.0.0", "upstream>=4"]
    assert updated["dependency-groups"]["dev"] == ["sample==2.0.0", "pytest>=8"]
