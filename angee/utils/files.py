"""Atomic file replacement shared by generators, settings writers and caches."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_atomic(path: Path, content: str | bytes) -> None:
    """Write UTF-8 text or bytes atomically, skipping an unchanged file.

    A concurrent reader (another Django boot importing a generated module, the
    Vite dev server reading the SDL) sees either the old file or the new one,
    never a half-written one: the bytes go to a temp file in the *same*
    directory and are then ``os.replace``-d into place, an atomic rename on the
    one filesystem. The unchanged-file short-circuit preserves the emitters'
    behaviour of not touching a file whose contents already match, so neither
    the autoreloader nor Vite sees a spurious modification. A failed write removes
    its own temp file so a leftover ``.tmp`` never lingers in ``runtime/`` to trip
    drift checks.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    if path.exists() and path.read_bytes() == data:
        return
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(data)
            tmp = Path(handle.name)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
