"""Intake capture, request access and work rules on emitted models, one composed host per profile."""

from pathlib import Path

import pytest

from tests.composed_host import run_composed_tests


@pytest.mark.parametrize("profile", [
    pytest.param({
        "app": "angee.intake",
        "sqlite": (
            "tests.native_intake_capture.ChannelIntakeCaptureTests",
            "tests.native_intake_capture.NeedAccessTests",
            "tests.native_intake_capture.NeedAccessDecisionTests",
            "tests.native_intake_capture.NeedResetAccessTests",
            "tests.native_intake_capture.DecisionRecordTests",
            "tests.native_intake_campaign.IntakeCampaign",
            "tests.native_intake_campaign.CaptureCampaign",
            "tests.native_projects_surface.ProjectSurfaceCampaign",
            "tests.test_work_task_access.TaskAccessTests",
            "tests.test_work_task_removal.TaskRemovalTests",
            "tests.test_work_record_merge.RecordMergeTests",
        ),
        "postgresql": (
            "tests.native_intake_capture.NeedAccessDecisionTests",
            "tests.native_intake_capture.NeedResetAccessTests",
        ),
    }, id="intake"),
    # Every addon's Hasura filter declarations build together once workflows joins intake.
    pytest.param({
        "app": ("angee.intake", "angee.workflows"),
        "sqlite": ("tests.native_intake_capture.HasuraFilterDeclarationTests",),
    }, id="intake-workflows"),
])
def test_composed_intake(tmp_path: Path, subtests: pytest.Subtests, profile: dict) -> None:
    run_composed_tests(tmp_path, subtests, **profile)
