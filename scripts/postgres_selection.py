"""Print the PostgreSQL lane's tests for a run, or the groups that split them.

The lane's selection is ``.github/postgres-selection.txt``: test files and node ids, one
per line. Reads on stdin the shared test paths from the ``changed_path_selection.py`` plan
(a reached directory reaches every entry under it), and prints the entries
whose test file is reached, one per line, with the examples profile's planner gate, which
budgets what any permission change adds to the console's reads. ``--all`` prints every
entry and reads nothing. ``--groups`` prints instead the groups, as a JSON list, that split
the printed entries' stored time (``.test_durations_postgres``) as finely as six groups
split the whole selection's.

    uv run --locked python scripts/postgres_selection.py [--groups] < reached-paths.txt
    uv run --locked python scripts/postgres_selection.py --all [--groups]
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / ".github/postgres-selection.txt"
MAP = ROOT / ".test_durations_postgres"
GROUPS = 6
PLANNER_GATE = "tests/test_composed_examples.py"


def entries(path: Path = SELECTION) -> list[str]:
    """Return the selection's entries in file order, without its comments and blank lines."""

    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def kept(selection: Iterable[str], reached: Iterable[str]) -> list[str]:
    """Return the planner gate and the entries whose test file a reached path names or contains."""

    roots = [path.rstrip("/") for path in reached]
    return [
        entry
        for entry in selection
        if (file := entry.partition("::")[0]) == PLANNER_GATE
        or any(file == root or file.startswith(f"{root}/") for root in roots)
    ]


def stored(entry: str, durations: Mapping[str, float]) -> float:
    """Return an entry's stored time, or one average test's when the map has none of its tests."""

    prefixes = (f"{entry}[", f"{entry}::")
    seconds = sum(time for test, time in durations.items() if test == entry or test.startswith(prefixes))
    return seconds or sum(durations.values()) / len(durations)


def groups(selection: Iterable[str], chosen: Iterable[str], durations: Mapping[str, float]) -> list[int]:
    """Return the groups that split ``chosen`` as finely as ``GROUPS`` split the whole ``selection``."""

    whole = sum(stored(entry, durations) for entry in selection)
    part = sum(stored(entry, durations) for entry in chosen)
    return list(range(1, min(GROUPS, max(1, math.ceil(GROUPS * part / whole))) + 1))


def main(argv: list[str]) -> None:
    """Print the entries a run selects, or with ``--groups`` the groups that split them."""

    if set(argv) - {"--all", "--groups"}:
        raise SystemExit(__doc__)
    selection = entries()
    chosen = selection if "--all" in argv else kept(selection, (line.strip() for line in sys.stdin if line.strip()))
    if "--groups" in argv:
        print(json.dumps(groups(selection, chosen, json.loads(MAP.read_text()))))
    else:
        print("\n".join(chosen))


if __name__ == "__main__":
    main(sys.argv[1:])
