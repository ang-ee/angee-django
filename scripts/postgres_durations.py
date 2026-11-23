"""Merge a main run's measured PostgreSQL test durations into the committed timing map.

A push to main records, for each group of the PostgreSQL lane, the durations of the
tests the group ran (pytest-split's store, into ``postgres-durations-<group>.json``)
and keeps each group's file as the run's ``postgres-durations-<group>`` artifact.
Given the run's id, this fetches those artifacts with ``gh run download``; given a
directory, it reads the files already downloaded under it. A measured entry replaces
the map's, and an entry no group ran is kept. The map is written as pytest-split
writes it, for a person to review and commit: CI never writes it.

    uv run --locked python scripts/postgres_durations.py <run-id or directory>
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / ".test_durations_postgres"
GROUP_FILES = "postgres-durations-*.json"


def merged(committed: Mapping[str, float], groups: Iterable[Mapping[str, float]]) -> dict[str, float]:
    """Return ``committed`` with each group's measured entries replacing its own, sorted by test id.

    The groups split one selection, so a test two groups measured means their files are
    not one run's: it is refused.
    """

    measured: dict[str, float] = {}
    for group in groups:
        if twice := sorted(measured.keys() & group.keys()):
            raise SystemExit(f"Two groups measured the same tests, so they are not one run's: {twice[:3]}")
        measured.update(group)
    return dict(sorted({**committed, **measured}.items()))


def main(argv: list[str], map_path: Path = MAP) -> None:
    """Merge the measured durations of a run, or of a directory of its group files, into ``map_path``."""

    if len(argv) != 1:
        raise SystemExit(__doc__)
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(argv[0])
        if argv[0].isdigit():
            directory = Path(scratch)
            subprocess.run(
                ["gh", "run", "download", argv[0], "--pattern", "postgres-durations-*", "--dir", scratch],
                cwd=ROOT, check=True,
            )
        files = sorted(directory.rglob(GROUP_FILES))
        if not files:
            raise SystemExit(f"No {GROUP_FILES} under {directory}: a main push run keeps one per group.")
        groups = [json.loads(path.read_text()) for path in files]
    committed = json.loads(map_path.read_text())
    result = merged(committed, groups)
    map_path.write_text(json.dumps(result, sort_keys=True, indent=4))
    measured = sum(len(group) for group in groups)
    added = len(result) - len(committed)
    print(
        f"Merged {len(files)} group file(s) ({', '.join(path.stem for path in files)}): "
        f"{measured - added} entries replaced, {added} added, {len(result) - measured} kept unmeasured."
    )


if __name__ == "__main__":
    main(sys.argv[1:])
