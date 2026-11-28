"""A main run's measured PostgreSQL durations replace the timing map's, and what no group ran is kept."""

import json
from pathlib import Path

import pytest
from scripts.postgres_durations import main, merged

COMMITTED = {
    "tests/test_a.py::test_kept": 1.0,
    "tests/test_b.py::test_measured_again": 9.0,
}


def test_a_measured_entry_replaces_the_maps_and_one_no_group_ran_is_kept() -> None:
    result = merged(COMMITTED, [{"tests/test_b.py::test_measured_again": 2.5}, {"tests/test_c.py::test_new": 4.0}])

    assert result == {
        "tests/test_a.py::test_kept": 1.0,
        "tests/test_b.py::test_measured_again": 2.5,
        "tests/test_c.py::test_new": 4.0,
    }
    assert list(result) == sorted(result)


def test_a_test_two_groups_measured_is_refused() -> None:
    with pytest.raises(SystemExit, match="not one run's"):
        merged(COMMITTED, [{"tests/test_c.py::test_new": 4.0}, {"tests/test_c.py::test_new": 5.0}])


def test_a_runs_downloaded_group_files_merge_into_the_map_as_pytest_split_writes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    for group, entries in ((1, {"tests/test_b.py::test_measured_again": 2.5}), (2, {"tests/test_c.py::test_new": 4.0})):
        artifact = run / f"postgres-durations-{group}"
        artifact.mkdir(parents=True)
        (artifact / f"postgres-durations-{group}.json").write_text(json.dumps(entries))
        (artifact / "postgresql-contracts.xml").write_text("<testsuites/>")
    durations = tmp_path / ".test_durations_postgres"
    durations.write_text(json.dumps(COMMITTED, sort_keys=True, indent=4))

    main([str(run)], map_path=durations)

    expected = merged(COMMITTED, [{"tests/test_b.py::test_measured_again": 2.5}, {"tests/test_c.py::test_new": 4.0}])
    assert durations.read_text() == json.dumps(expected, sort_keys=True, indent=4)
    assert "1 entries replaced, 1 added, 1 kept unmeasured" in capsys.readouterr().out


def test_a_directory_without_group_files_writes_nothing(tmp_path: Path) -> None:
    durations = tmp_path / ".test_durations_postgres"
    durations.write_text(json.dumps(COMMITTED, sort_keys=True, indent=4))

    with pytest.raises(SystemExit, match="one per group"):
        main([str(tmp_path / "empty")], map_path=durations)

    assert json.loads(durations.read_text()) == COMMITTED
