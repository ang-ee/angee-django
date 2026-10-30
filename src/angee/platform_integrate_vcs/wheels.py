"""Bounded data-only readers for explicitly built wheel distributions."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

MAX_CATALOGUE_BYTES = 4 * 1024 * 1024
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


def wheel_metadata(wheel: Path | BinaryIO) -> dict[str, str]:
    """Read bounded native dist-info and packaged capability documents, never code."""

    if isinstance(wheel, Path):
        size = wheel.stat().st_size
    else:
        wheel.seek(0, 2)
        size = wheel.tell()
        wheel.seek(0)
    if size > MAX_WHEEL_BYTES:
        raise ValueError("Built wheel exceeds its archive byte limit")
    with zipfile.ZipFile(wheel) as archive:
        members = archive.infolist()
        if len(members) > 10000 or len({member.filename for member in members}) != len(members):
            raise ValueError("Wheel file inventory is oversized or ambiguous")
        for member in members:
            relative_path(member.filename[:-1] if member.is_dir() else member.filename)
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
            if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError("Wheel metadata uses an unsupported compression method")
            with archive.open(member) as stream:
                content = stream.read(limit + 1)
            if len(content) > limit:
                raise ValueError("Wheel metadata file exceeds its byte limit")
            files[member.filename] = content.decode("utf-8")
        record_name = f"{directory}/RECORD"
        # Transport only the original inventory rows for returned data files;
        # code/asset inventories are not needed by native package-data readers.
        record = io.StringIO()
        csv.writer(record).writerows(row for row in validate_record(files[record_name]) if row[0] in files)
        files[record_name] = record.getvalue()
        if len(files[record_name].encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError("Wheel metadata inventory exceeds its byte limit")
        return files
