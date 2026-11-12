"""Run PM demo loading against the area's complete emitted model graph."""

from tests.composed_host import run_composed_tests


def test_pm_demo_loads_and_replays(tmp_path, subtests):
    run_composed_tests(tmp_path, subtests, app="angee.pm", sqlite=("tests.native_pm_demo",))
