"""Exercise access decisions on the composed account, party and request owners."""

from pathlib import Path

from tests.test_composed_intake_capture import run_intake_tests


def test_intake_access_decision(tmp_path: Path) -> None:
    run_intake_tests(tmp_path, "NeedAccessDecisionTests")
