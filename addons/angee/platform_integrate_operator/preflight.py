"""Native uv dependency edits, checked before the held stack environment changes.

The temporary filesystem view preserves relative source paths for uv; it does
not parse package metadata or resolve requirements. The operator owns stopping
consumers before invoking this worker and leaves them stopped on any failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from django.core.files import locks
from django_compose.artifacts import atomic_write


@contextmanager
def project_view(project: Path) -> Iterator[Path]:
    """Copy writable host metadata; mirror other paths as read-only uv inputs.

    Recreate each ancestor's directory entries so both child and sibling relative
    sources resolve to the same authored files. uv writes only the copied host
    pyproject and lock. Its dry-run workspace report is checked before any edit.
    """
    with tempfile.TemporaryDirectory(prefix="angee-uv-plan-") as temporary:
        root = Path(temporary) / "view"
        root.mkdir()
        destination = root
        ancestors = (*reversed(project.parents), project)
        for index, source in enumerate(ancestors):
            child = ancestors[index + 1].name if index + 1 < len(ancestors) else None
            for entry in source.iterdir():
                target = destination / entry.name
                if entry.name == child:
                    target.mkdir()
                elif source == project and entry.name in {"pyproject.toml", "uv.lock"}:
                    shutil.copyfile(entry, target)
                else:
                    target.symlink_to(entry, target_is_directory=entry.is_dir())
            if child is not None:
                destination /= child
        yield destination


def _uv(project: Path, environment: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["uv", "--project", str(project), *arguments],
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)},
        cwd=project, check=True, capture_output=True, text=True,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.stdout


def _plan(project: Path, environment: Path, options: Sequence[str]) -> dict[str, Any]:
    plan = json.loads(_uv(project, environment, "sync", "--dry-run", "--output-format", "json", *options))
    if not isinstance(plan, dict) or plan.get("dry_run") is not True:
        raise ValueError("uv did not return a dry-run sync plan")
    # A nested workspace's writable lock belongs to its native root, not to this
    # host copy. Refuse before uv add/remove could mutate that external owner.
    if Path(plan["project"]["workspace"]["path"]).resolve() != project.resolve():
        raise ValueError("Dependency changes must run at the native uv workspace root")
    if Path(plan["sync"]["environment"]["path"]).resolve() != environment.resolve():
        raise ValueError("uv planned a different environment from the stack .venv")
    return plan


def removal_set(plan: dict[str, Any]) -> tuple[str, ...]:
    """Read complete removals from uv's plan, excluding version replacements."""
    installed: set[str] = set()
    uninstalled: set[str] = set()
    for change in plan["sync"]["changes"]:
        name, action = change["name"], change["action"]
        if not isinstance(name, str) or not name:
            raise ValueError("uv returned an invalid distribution name")
        if action == "installed":
            installed.add(name)
        elif action == "uninstalled":
            uninstalled.add(name)
        else:
            raise ValueError(f"Unsupported uv sync action: {action}")
    return tuple(sorted(uninstalled - installed))


def _publish(project: Path, before: dict[str, bytes | None], after: dict[str, bytes]) -> None:
    """CAS both authored files before publishing either; roll back a partial pair."""
    for name, expected in before.items():
        path = project / name
        current = path.read_bytes() if path.exists() else None
        if current != expected:
            raise RuntimeError(f"{name} changed during dependency preflight; retry from current metadata")
    published: list[str] = []
    try:
        for name, content in after.items():
            atomic_write(project / name, content)
            published.append(name)
    except BaseException:
        for name in reversed(published):
            original = before[name]
            if original is None:
                (project / name).unlink()
            else:
                atomic_write(project / name, original)
        raise


def change_dependencies(
    project: Path, action: Literal["add", "remove"], requirement: str, *, no_editable: bool = False,
) -> tuple[str, ...]:
    """Resolve with uv, audit complete removals, publish its exact lock, then sync.

    The process must be the operator's held install job. The native compiler
    audit runs in the unchanged stack interpreter before any metadata publication
    or environment mutation. A failed sync retains the checked desired metadata
    for an explicit retry while the operator keeps application services stopped.
    """
    project = project.resolve()
    environment = project / ".venv"
    if not (environment / "bin" / "python").is_file():
        raise ValueError("Dependency preflight requires the existing stack .venv")
    if action not in {"add", "remove"} or not requirement or requirement.startswith("-"):
        raise ValueError("Supply an add/remove action and a distribution requirement")
    options = ("--no-editable",) if no_editable else ()
    with (project / ".angee-dependencies.lock").open("a+b") as lock:
        if not locks.lock(lock, locks.LOCK_EX):
            raise OSError("Could not lock project dependency metadata")
        try:
            before = {
                name: (project / name).read_bytes() if (project / name).exists() else None
                for name in ("pyproject.toml", "uv.lock")
            }
            with project_view(project) as scratch:
                _plan(scratch, environment, options)
                _uv(scratch, environment, action, "--no-sync", "--", requirement)
                plan = _plan(scratch, environment, ("--locked", *options))
                removing = removal_set(plan)
                if removing:
                    subprocess.run(
                        [str(environment / "bin" / "python"), "manage.py", "check_migrations", "--imports",
                         "--removing", *removing], cwd=project, check=True,
                    )
                after = {name: (scratch / name).read_bytes() for name in before}
                _publish(project, before, after)
            _uv(project, environment, "sync", "--locked", *options)
            return removing
        finally:
            locks.unlock(lock)


def main() -> None:
    """Run the operator install job without populating Django in this worker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("add", "remove"))
    parser.add_argument("requirement")
    parser.add_argument("--no-editable", action="store_true")
    options = parser.parse_args()
    try:
        change_dependencies(Path.cwd(), options.action, options.requirement, no_editable=options.no_editable)
    except subprocess.CalledProcessError as error:
        if error.stderr:
            sys.stderr.write(error.stderr)
        raise SystemExit(error.returncode) from error


if __name__ == "__main__":
    main()
