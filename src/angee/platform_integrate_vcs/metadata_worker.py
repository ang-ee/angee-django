"""Trusted metadata program executed only by the offline operator build job.

Importing this module loads only the standard library. The serving caller sends
its source as the job artifact; build frontends/backends run only in that job.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_OUTPUT_BYTES = 262144
MAX_METADATA_BYTES = 65536
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_PACKAGES = 128
MAX_FILES = 256


def relative_path(value: str, *, root: bool = False) -> PurePosixPath:
    """Accept normalized source-relative POSIX names, never traversal or aliases."""

    path = PurePosixPath(value)
    if not value or "\\" in value or any(ord(char) < 32 for char in value) or path.is_absolute() or ".." in path.parts:
        raise ValueError("Metadata paths must be normalized relative POSIX paths")
    if value != path.as_posix() or (path == PurePosixPath(".") and not root):
        raise ValueError("Metadata paths must be normalized relative POSIX paths")
    return path


def validate_record(content: str) -> list[list[str]]:
    """Validate native RECORD paths before importlib performs filesystem lookups."""

    rows = list(csv.reader(content.splitlines()))
    for row in rows:
        if not row or len(row) > 3:
            raise ValueError("Invalid native wheel RECORD row")
        relative_path(row[0])
    return rows


def wheel_metadata(wheel: Path) -> dict[str, str]:
    """Read bounded native dist-info and packaged capability documents, never code."""

    if wheel.stat().st_size > MAX_WHEEL_BYTES:
        raise ValueError("Built wheel exceeds its archive byte limit")
    with zipfile.ZipFile(wheel) as archive:
        members = archive.infolist()
        if len(members) > 10000 or len({member.filename for member in members}) != len(members):
            raise ValueError("Wheel file inventory is oversized or ambiguous")
        directories = {PurePosixPath(member.filename).parts[0] for member in members
                       if PurePosixPath(member.filename).parts and
                       PurePosixPath(member.filename).parts[0].endswith(".dist-info")}
        if len(directories) != 1:
            raise ValueError("A built wheel must contain one native dist-info directory")
        directory = directories.pop()
        required = {f"{directory}/METADATA", f"{directory}/RECORD"}
        selected = [member for member in members if member.filename in {
            *required, f"{directory}/entry_points.txt",
        } or member.filename.endswith("/pyproject.toml")]
        if not required <= {member.filename for member in selected} or len(selected) > MAX_FILES:
            raise ValueError("Wheel metadata is missing or oversized")
        files = {}
        for member in selected:
            relative_path(member.filename)
            limit = 1024 * 1024 if member.filename == f"{directory}/RECORD" else MAX_METADATA_BYTES
            if member.file_size > limit:
                raise ValueError("Wheel metadata file exceeds its byte limit")
            files[member.filename] = archive.read(member).decode("utf-8")
        record_name = f"{directory}/RECORD"
        # Transport only the original inventory rows for returned data files;
        # code/asset inventories are not needed by native package-data readers.
        record = io.StringIO()
        csv.writer(record).writerows(row for row in validate_record(files[record_name]) if row[0] in files)
        files[record_name] = record.getvalue()
        if len(files[record_name].encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError("Wheel metadata inventory exceeds its byte limit")
        return files


def prepare(repository: Path, package_path: str) -> dict:
    """Build all projects in a bounded source scope using preinstalled dependencies."""

    from build import ProjectBuilder

    repository = repository.resolve(strict=True)
    scope = (repository / relative_path(package_path, root=True)).resolve(strict=True)
    if not scope.is_relative_to(repository) or not scope.is_dir():
        raise ValueError("Package path escapes the source repository")
    projects = []
    for directory, subdirectories, files in scope.walk(follow_symlinks=False):
        subdirectories[:] = sorted(name for name in subdirectories if name != ".git")
        if "pyproject.toml" in files:
            marker = directory / "pyproject.toml"
            if not marker.resolve(strict=True).is_relative_to(repository):
                raise ValueError("Project marker escapes the source repository")
            projects.append(directory)
        if len(projects) > MAX_PACKAGES:
            raise ValueError("Source contains too many build projects")
    packages = []
    with tempfile.TemporaryDirectory(prefix="catalogue-metadata-", dir=repository.parent) as temporary:
        output = Path(temporary)
        for index, project in enumerate(sorted(projects)):
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
                def runner(command, cwd=None, extra_environ=None):
                    subprocess.run(
                        command, cwd=cwd, env={**os.environ, **(extra_environ or {})},
                        stdout=log, stderr=subprocess.STDOUT, check=True,
                    )
                try:
                    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                        builder = ProjectBuilder(str(project), runner=runner)
                        missing = builder.check_dependencies("wheel")
                        if missing:
                            raise ValueError("Prepared build image lacks dependencies: " + "; ".join(
                                " -> ".join(chain) for chain in sorted(missing)
                            ))
                        wheel = Path(builder.build("wheel", str(output / str(index))))
                    built_files = wheel_metadata(wheel)
                except Exception as error:
                    log.seek(0)
                    detail = log.read(4096).strip()
                    raise RuntimeError(f"Native wheel metadata build failed for {project.relative_to(repository)}: "
                                       f"{error}; {detail}") from error
            packages.append({"path": project.relative_to(repository).as_posix(), "files": built_files})
            if len(json.dumps({"schema": 1, "packages": packages}).encode("utf-8")) > MAX_OUTPUT_BYTES:
                raise ValueError("Catalogue metadata exceeds its output byte limit")
    return {"schema": 1, "packages": packages}


if __name__ == "__main__":
    print(json.dumps(prepare(Path(sys.argv[1]), os.environ.get("PACKAGE_PATH", ".")), sort_keys=True))
