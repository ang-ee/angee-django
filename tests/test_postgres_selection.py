"""A pull request's PostgreSQL groups run the selection's entries its changed paths reach, sized by stored time."""

from pathlib import Path

from scripts.postgres_selection import PLANNER_GATE, entries, groups, kept

ROOT = Path(__file__).resolve().parents[1]
SELECTION = [
    PLANNER_GATE,
    "tests/test_locks.py",
    "tests/test_races.py::test_two_writers",
    "tests/test_races.py::test_parametrized",
    "src/angee/resources/tests/test_ledger.py",
]
DURATIONS = {
    f"{PLANNER_GATE}::test_budget": 10.0,
    "tests/test_locks.py::test_a": 30.0,
    "tests/test_locks.py::test_b": 30.0,
    "tests/test_races.py::test_two_writers": 20.0,
    "tests/test_races.py::test_two_writers_on_one_row": 99.0,
    "tests/test_races.py::test_parametrized[left]": 5.0,
    "tests/test_races.py::test_parametrized[right]": 5.0,
}


def test_every_entry_names_a_test_file_in_the_repository() -> None:
    selection = entries()

    assert len(selection) == len(set(selection))
    assert PLANNER_GATE in selection
    assert [entry for entry in selection if not (ROOT / entry.partition("::")[0]).is_file()] == []


def test_a_reached_file_keeps_its_whole_file_and_node_entries_with_the_planner_gate() -> None:
    assert kept(SELECTION, ["tests/test_races.py", "tests/test_unlisted.py"]) == [
        PLANNER_GATE,
        "tests/test_races.py::test_two_writers",
        "tests/test_races.py::test_parametrized",
    ]


def test_a_reached_directory_keeps_every_entry_under_it_and_nothing_reached_keeps_the_gate() -> None:
    assert kept(SELECTION, ["tests", "src/angee/resources/tests"]) == SELECTION
    assert kept(SELECTION, []) == [PLANNER_GATE]


def test_the_groups_split_the_kept_entries_as_finely_as_six_split_the_whole_selection() -> None:
    # Whole: 10 + 60 + 20 + 10 + one average test (about 28.4) for the unmeasured ledger file.
    assert groups(SELECTION, SELECTION, DURATIONS) == [1, 2, 3, 4, 5, 6]
    assert groups(SELECTION, ["tests/test_locks.py"], DURATIONS) == [1, 2, 3]
    assert groups(SELECTION, [PLANNER_GATE], DURATIONS) == [1]
