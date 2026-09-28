"""Verify intake capture on emitted models, not a mocked registry."""

from pathlib import Path

from tests.composed_host import run_composed_tests


def run_intake_tests(tmp_path: Path, test_class: str) -> None:
    """Run one intake contract group against real emitted models in isolation."""
    run_composed_tests(tmp_path, f"tests.native_intake_capture.{test_class}", app="angee.intake")


def test_composed_intake_capture(tmp_path: Path) -> None:
    run_intake_tests(tmp_path, "ChannelIntakeCaptureTests")
