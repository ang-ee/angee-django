"""Work contracts that need the complete emitted Task graph, one composed host per profile."""

from pathlib import Path

import pytest

from tests.composed_host import run_composed_tests


@pytest.mark.parametrize("profile", [
    pytest.param({
        "app": "angee.work",
        "sqlite": (
            "tests.test_work_task_access.PublicQueueTests",
            "tests.test_work_task_access.QueuePermissionsTests",
            "tests.test_work_task_access.SchemaImportTests",
            "tests.test_work_task_access.DuplicateLinkTests",
            "tests.test_work_task_actions.TaskActionTests",
            "tests.test_work_task_audience.TaskAudienceTests",
            "tests.test_work_task_status.TaskStatusTests",
            "tests.test_work_stage_lines.StageLinesTests",
            "tests.test_work_rule_stages.RuleStageTests",
            "tests.test_work_project_follow.ProjectFollowTests",
            "tests.test_work_identity.WorkStageResourceIdentityTests",
            "tests.test_projects_record_tasks_graphql.RecordTaskGraphQLTests",
            "tests.test_task_project_ordering.TaskProjectOrderingTests",
        ),
        "postgresql": ("tests.test_task_project_ordering.TaskProjectOrderingTests",),
    }, id="work"),
    pytest.param({"app": ("angee.work", "angee.mcp"), "sqlite": ("tests.native_work_campaign",)}, id="work-mcp"),
])
def test_composed_work(tmp_path: Path, subtests: pytest.Subtests, profile: dict) -> None:
    run_composed_tests(tmp_path, subtests, **profile)
