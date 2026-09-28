"""Exercise request visibility and holder authorization on the composed graph."""

from pathlib import Path

from tests.test_composed_intake_capture import run_intake_tests


def test_intake_access(tmp_path: Path) -> None:
    run_intake_tests(tmp_path, "NeedAccessTests")
