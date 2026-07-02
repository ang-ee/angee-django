"""Project-root discovery shared by Angee boot entrypoints."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from angee.paths import resolve_path

PROJECT_DIR_ENV = "ANGEE_PROJECT_DIR"
PROJECT_SETTINGS_ENV = "ANGEE_PROJECT_SETTINGS"
PROJECT_YAML_NAME = "settings"


def has_project_contract(path: Path) -> bool:
    """Return whether ``path`` declares an Angee project settings contract."""

    return (path / "settings.yaml").exists() or (path / "settings.py").exists()


def find_project_dir(
    *,
    environ: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
    cwd: Path | str | None = None,
) -> Path | None:
    """Return the project root when it can be discovered without failing.

    Discovery is deliberately ordered: explicit ``ANGEE_PROJECT_DIR`` first,
    then the directory containing a ``manage.py`` entrypoint, then the nearest
    ancestor of the current working directory that carries ``settings.yaml`` or
    ``settings.py``.
    """

    environment = os.environ if environ is None else environ
    configured = environment.get(PROJECT_DIR_ENV)
    if configured:
        return resolve_path(configured)

    arguments = sys.argv if argv is None else argv
    if arguments:
        argv0 = Path(arguments[0]).resolve()
        if argv0.name == "manage.py":
            return argv0.parent

    start = Path.cwd().resolve() if cwd is None else resolve_path(cwd)
    for candidate in (start, *start.parents):
        if has_project_contract(candidate):
            return candidate
    return None


def project_dir(
    *,
    environ: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
    cwd: Path | str | None = None,
) -> Path:
    """Return the discovered project root, or fail with a boot error."""

    discovered = find_project_dir(environ=environ, argv=argv, cwd=cwd)
    if discovered is None:
        raise ImproperlyConfigured(
            "Angee needs ANGEE_PROJECT_DIR or a project root containing settings.yaml/settings.py"
        )
    return discovered
