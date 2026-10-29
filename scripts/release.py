"""Bump the framework release cohort and update its workspace consumers."""

from __future__ import annotations

import argparse
import subprocess
import tomllib
from pathlib import Path

import tomlkit
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version


def projects(root: Path) -> tuple[Path, ...]:
    """Return the root and every actual native uv workspace member in order."""

    data = tomllib.loads((root / "pyproject.toml").read_text())
    members = {
        path
        for pattern in data["tool"]["uv"]["workspace"]["members"]
        for path in root.glob(pattern)
        if path.is_dir() and (path / "pyproject.toml").is_file()
    }
    return (root, *sorted(members))


def cohort(root: Path, members: tuple[Path, ...]) -> tuple[Path, ...]:
    """The collection and production framework packages share a release version."""

    return (root, *(path for path in members if path.parent == root / "addons/angee"))


def update_consumers(root: Path, members: tuple[Path, ...], targets: tuple[Path, ...]) -> None:
    """Update affected native requirements without bumping consumer versions."""

    versions = {
        canonicalize_name(project["name"]): project["version"]
        for path in targets
        if (project := tomllib.loads((path / "pyproject.toml").read_text())["project"])
    }

    def requirement(raw: str, *, pin: bool = False) -> str:
        parsed = Requirement(raw)
        version = versions.get(canonicalize_name(parsed.name))
        if version is None:
            return raw
        major = Version(version).major
        parsed.specifier = SpecifierSet(
            f"=={version}"
            if pin or any(item.operator in {"==", "==="} for item in parsed.specifier)
            else f">={major},<{major + 1}"
        )
        return str(parsed)

    for member in members:
        path = member / "pyproject.toml"
        data = tomlkit.parse(path.read_text())
        project = data["project"]
        if "dependencies" in project:
            project["dependencies"] = [requirement(raw, pin=member == root) for raw in project["dependencies"]]
        for extra, values in project.get("optional-dependencies", {}).items():
            project["optional-dependencies"][extra] = [requirement(raw) for raw in values]
        for name, values in data.get("dependency-groups", {}).items():
            data["dependency-groups"][name] = [requirement(raw) if isinstance(raw, str) else raw for raw in values]
        if "build-system" in data:
            data["build-system"]["requires"] = [requirement(raw) for raw in data["build-system"]["requires"]]
        path.write_text(tomlkit.dumps(data))


def bump(root: Path, kind: str) -> None:
    members = projects(root)
    targets = cohort(root, members[1:])
    for path in targets:
        name = tomllib.loads((path / "pyproject.toml").read_text())["project"]["name"]
        subprocess.run(
            ["uv", "version", "--package", name, "--bump", kind, "--frozen"], cwd=root, check=True
        )
    update_consumers(root, members, targets)
    subprocess.run(["uv", "lock"], cwd=root, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--bump", choices=("major", "minor", "patch", "stable", "alpha", "beta", "rc", "post", "dev"))
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.bump:
        bump(root, args.bump)
    if args.build:
        for path in cohort(root, projects(root)[1:]):
            name = tomllib.loads((path / "pyproject.toml").read_text())["project"]["name"]
            subprocess.run(["uv", "build", "--package", name, "--wheel", "--no-sources"], cwd=root, check=True)
    if not args.bump and not args.build:
        parser.error("choose --bump or --build")


if __name__ == "__main__":
    main()
