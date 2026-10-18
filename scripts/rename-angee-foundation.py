#!/usr/bin/env python3
"""Move the framework foundation into the root package and rewrite consumers."""

from __future__ import annotations

import re
import shutil
from pathlib import Path


REPOSITORY_NAMES = (
    "angee",
    "angee-arp",
    "angee-odoo",
    "angee-messaging-bridges",
    "angee-phrogg",
)
SKIPPED_DIRECTORIES = frozenset({
    ".git",
    ".jj",
    ".mypy_cache",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "test-results",
})
OLD_DOTTED = "angee" + ".base"
OLD_PATH = "angee" + "/base"
PATH_REFERENCE = re.compile(rf"(?<!@){re.escape(OLD_PATH)}")
CONCERN_PATHS = (
    # Main split the former manager module into the models package; an older
    # guide on the merge side still names its pre-split physical path.
    (f"{OLD_PATH}/managers.py", "angee/models/manager.py"),
)


def move_foundation(framework: Path) -> int:
    """Move every source child into ``angee/``, preserving the models package."""

    package = framework / "angee"
    source = package / ("ba" + "se")
    if not source.exists():
        return 0
    cache = source / "__pycache__"
    if cache.exists():
        shutil.rmtree(cache)
    moved = 0
    for child in sorted(source.iterdir()):
        destination = package / child.name
        if destination.exists():
            raise FileExistsError(f"rename collision: {child} -> {destination}")
        child.rename(destination)
        moved += 1
    source.rmdir()
    return moved


def source_files(repository: Path):
    """Yield UTF-8 source candidates while excluding generated and VCS trees."""

    pending = [repository]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            if path.is_symlink():
                continue
            if path.is_dir():
                if path.name not in SKIPPED_DIRECTORIES:
                    pending.append(path)
                continue
            if path.is_file():
                yield path


def rewrite_repository(repository: Path) -> tuple[int, int]:
    """Rewrite Python namespace and physical source-path references in one repository."""

    files = 0
    replacements = 0
    for path in source_files(repository):
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        dotted_count = original.count(OLD_DOTTED)
        rewritten = original.replace(OLD_DOTTED, "angee")
        concern_count = 0
        for old, new in CONCERN_PATHS:
            rewritten, count = re.subn(rf"(?<!@){re.escape(old)}", new, rewritten)
            concern_count += count
        rewritten, path_count = PATH_REFERENCE.subn("angee", rewritten)
        if rewritten == original:
            continue
        path.write_text(rewritten, encoding="utf-8")
        files += 1
        replacements += dotted_count + concern_count + path_count
    return files, replacements


def main() -> None:
    framework = Path(__file__).resolve().parents[1]
    workspace = framework.parent
    moved = move_foundation(framework)
    print(f"angee: moved {moved} foundation entries")
    for name in REPOSITORY_NAMES:
        repository = workspace / name
        if not repository.is_dir():
            raise FileNotFoundError(f"missing repository: {repository}")
        files, replacements = rewrite_repository(repository)
        print(f"{name}: {replacements} replacements across {files} files")


if __name__ == "__main__":
    main()
