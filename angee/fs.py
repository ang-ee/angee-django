"""Filesystem primitives shared by Angee's runtime and SDL emitters.

A namespace-root utility (peer to :mod:`angee.paths`): both the build-time
composer (:mod:`angee.compose.runtime`) and the GraphQL SDL owner
(:mod:`angee.graphql.sdl`) write generated files through one primitive, so the
atomic-write behaviour lives once.
"""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

GENERATED_SENTINEL = "# ANGEE GENERATED RUNTIME - DO NOT EDIT"
"""Marker every Angee-generated file carries; the gate before destructive cleanup.

Lives here, beside :func:`write_atomic`, because it is the sentinel for *all*
generated files — Python runtime modules, the GraphQL SDL, and the composed
``runtime/web`` artifacts — so both the composer and the web projector import it
from one namespace-root owner rather than from each other.
"""


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, skipping an unchanged file.

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
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            tmp = Path(handle.name)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
