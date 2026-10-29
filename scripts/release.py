"""Bump a native uv workspace together and build its release wheels."""

from __future__ import annotations

import argparse
import subprocess
import tomllib
from pathlib import Path

import tomlkit
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version


def projects(root: Path) -> tuple[Path, ...]:
    """Read native workspace membership, with deterministic directory ordering."""

    data = tomllib.loads((root / "pyproject.toml").read_text())
    members = {
        path for pattern in data["tool"]["uv"]["workspace"]["members"] for path in root.glob(pattern) if path.is_dir()
    }
    return (root, *sorted(members))


def pin_meta(root: Path, members: tuple[Path, ...]) -> None:
    """Keep existing meta requirements pinned to their owned member versions."""

    versions = {}
    for path in members:
        project = tomllib.loads((path / "pyproject.toml").read_text())["project"]
        versions[project["name"]] = project["version"]
    target = root / "pyproject.toml"
    data = tomlkit.parse(target.read_text())
    data["project"]["dependencies"] = [
        f"{name}=={versions[name]}" if (name := Requirement(raw).name) in versions else raw
        for raw in data["project"]["dependencies"]
    ]
    # Examples and fixture distributions stay test-only requirements.
    for group, requirements in data.get("dependency-groups", {}).items():
        data["dependency-groups"][group] = [
            f"{name}=={versions[name]}"
            if isinstance(requirement, str) and (name := Requirement(requirement).name) in versions
            else requirement
            for requirement in requirements
        ]
    for member in members:
        path = member / "pyproject.toml"
        package = tomlkit.parse(path.read_text())
        dependencies = []
        for raw in package["project"].get("dependencies", []):
            requirement = Requirement(raw)
            if requirement.name in versions:
                major = Version(versions[requirement.name]).major
                requirement.specifier = SpecifierSet(f">={major},<{major + 1}")
                raw = str(requirement)
            dependencies.append(raw)
        package["project"]["dependencies"] = dependencies
        path.write_text(tomlkit.dumps(package))
    target.write_text(tomlkit.dumps(data))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--bump", choices=("major", "minor", "patch", "stable", "alpha", "beta", "rc", "post", "dev"))
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    members = projects(root)
    if args.bump:
        subprocess.run(["uv", "version", "--bump", args.bump, "--frozen", "--no-sync"], cwd=root, check=True)
        version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        for member in members[1:]:
            name = tomllib.loads((member / "pyproject.toml").read_text())["project"]["name"]
            subprocess.run(["uv", "version", version, "--package", name, "--frozen", "--no-sync"], cwd=root, check=True)
        pin_meta(root, members[1:])
        subprocess.run(["uv", "lock"], cwd=root, check=True)
    if args.build:
        subprocess.run(["uv", "build", "--all-packages", "--wheel", "--no-sources"], cwd=root, check=True)
    if not args.bump and not args.build:
        parser.error("choose --bump or --build")


if __name__ == "__main__":
    main()
