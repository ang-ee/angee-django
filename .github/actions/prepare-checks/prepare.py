"""Install an exact CI environment from the source slots' native locks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tomllib
from pathlib import Path


def uv(*args: str | Path, cwd: Path, output: bool = False) -> str:
    result = subprocess.run(
        ["uv", *map(str, args)], cwd=cwd, text=True, check=True, stdout=subprocess.PIPE if output else None,
    )
    return result.stdout or ""


def framework_requirements(stack: Path, framework: Path, python: Path) -> list[str]:
    """Select the framework distributions and extras the rendered host requires."""
    # Parse the authored requirements with the native dependency syntax owner.
    manifest = tomllib.loads((stack / "pyproject.toml").read_text())
    sources = manifest["tool"]["uv"]["sources"]
    names = {
        name for name, source in sources.items()
        if isinstance(source, dict)
        and "path" in source
        and (stack / source["path"]).resolve().is_relative_to(framework)
    }
    requirements = subprocess.check_output(
        [str(python), "-c",
         "from packaging.requirements import Requirement; import json, sys;"
         "print(json.dumps([(r.name, sorted(r.extras)) for r in map(Requirement, sys.argv[1:])]))",
         *manifest["project"]["dependencies"]],
        text=True,
    )
    flags = ["--no-default-groups", "--group", "checks"]
    for name, extras in json.loads(requirements):
        if name in names:
            flags.extend(("--package", name))
            for extra in extras:
                flags.extend(("--extra", extra))
    return flags


def export(project: Path, target: Path, flags: list[str], *, lock: str = "--locked") -> None:
    uv("export", lock, "--format", "pylock.toml", "--quiet", "--output-file", target, *flags, cwd=project)


def install_missing(stack: Path, python: Path, project: Path, target: Path, flags: list[str], *, lock: str) -> None:
    installed = json.loads(uv("pip", "list", "--python", python, "--format", "json", cwd=stack, output=True))
    omissions = [value for package in installed for value in ("--no-emit-package", package["name"])]
    export(project, target, [*flags, *omissions], lock=lock)
    uv("pip", "install", "--python", python, "--no-deps", "-r", target, cwd=stack)


def prepare(stack: Path, slot: str, extra: str, mixed_bridges: bool) -> Path | None:
    sources = stack / "workspaces" / "src"
    framework = sources / "angee"
    caller = sources / slot
    python = stack / ".venv" / "bin" / "python"
    uv("venv", "--managed-python", "--python", os.environ["UV_PYTHON"], python.parents[1], cwd=stack)
    # uv writes directory records relative to the exported project. Leave each
    # native export there so its editable source identities remain intact.
    primary = caller / "pylock.ci-caller.toml"
    export(caller, primary, ["--extra", extra] if extra else [])
    uv("pip", "sync", "--python", python, primary, cwd=stack)
    donor = None
    if caller != framework:
        donor = framework / "pylock.ci-framework.toml"
        flags = framework_requirements(stack, framework, python)
        export(framework, donor, flags)
        install_missing(stack, python, framework, framework / "pylock.ci-supplement.toml", flags, lock="--locked")
    elif mixed_bridges:
        # The exact bridge ref supplies its own locked artifacts. Its outer
        # framework records can refer to an older checkout, so the framework
        # primary owns all overlaps and compatibility is checked after install.
        bridges = sources / "angee-messaging-bridges"
        donor = bridges / "pylock.ci-bridges.toml"
        export(bridges, donor, [], lock="--frozen")
        install_missing(stack, python, bridges, bridges / "pylock.ci-supplement.toml", [], lock="--frozen")
    uv("pip", "check", "--python", python, cwd=stack)
    return donor


def configure(stack: Path, roots: str, sources: str, profile: str) -> None:
    """Write only the authored stack inputs that differ for this invocation."""
    subprocess.run(
        [str(stack / ".venv" / "bin" / "python"), "-c", """
import json
import sys
from pathlib import Path

import yaml

roots, sources = map(json.loads, sys.argv[1:3])
settings = Path("settings.yaml")
document = yaml.safe_load(settings.read_text())
if roots:
    document["INSTALLED_APPS"] = roots
elif sys.argv[3] == "full":
    document["INSTALLED_APPS"].append("angee.messaging_integrate_matrix")
settings.write_text(yaml.safe_dump(document, sort_keys=False))
if sources:
    path = Path("pnpm-workspace.yaml")
    workspace = yaml.safe_load(path.read_text())
    workspace["packages"].extend(f"workspaces/src/{source['path']}/src/*/*/web" for source in sources)
    path.write_text(yaml.safe_dump(workspace, sort_keys=False))
""", roots, sources, profile],
        cwd=stack,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot")
    parser.add_argument("--extra", default="")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--roots", default="[]")
    parser.add_argument("--sources", default="[]")
    options = parser.parse_args()
    stack = Path.cwd().resolve()
    donor = prepare(stack, options.slot, options.extra, options.slot == "angee" and options.profile == "full")
    configure(stack, options.roots, options.sources, options.profile)
    if donor is not None and (output := os.environ.get("GITHUB_OUTPUT")):
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"donor={donor}\n")


if __name__ == "__main__":
    main()
