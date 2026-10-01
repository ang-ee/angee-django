"""Exercise subject and successor verbs on the emitted intake composition."""

from pathlib import Path

from tests.test_composed_intake_capture import run_intake_tests


def test_intake_record_verbs(tmp_path: Path) -> None:
    run_intake_tests(tmp_path, "DecisionRecordTests")
