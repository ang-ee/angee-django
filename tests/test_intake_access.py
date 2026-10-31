"""Exercise request visibility and holder authorization on the composed graph."""

from pathlib import Path

from tests.composed_host import run_composed_tests


def test_intake_access(tmp_path: Path) -> None:
    run_composed_tests(
        tmp_path,
        "tests.native_intake_capture.NeedAccessTests",
        app=("angee.intake", "angee.projects_knowledge"),
    )
