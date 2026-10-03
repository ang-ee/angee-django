"""Run PM demo loading against the area's complete emitted model graph."""

from tests.composed_host import run_composed_tests


def test_pm_demo_loads_and_replays(tmp_path):
    run_composed_tests(tmp_path, "tests.native_pm_demo", app="angee.pm")
