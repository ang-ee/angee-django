"""Focused emitted-model checks for record delivery, wake and ownership verbs."""

from pathlib import Path

import pytest

from tests.composed_host import run_composed_tests


@pytest.mark.parametrize("app, labels", [
    (("angee.posts", "angee.mcp"), ("tests.native_messaging_delivery.DeliveryActionAdmission",)),
    ("angee.work", (
        "tests.test_work_task_actions.TaskActionTests",
        "tests.test_projects_record_tasks_graphql.RecordTaskGraphQLTests.test_transfer_permissions_are_projected_and_project_transfer_retains_attribution",
    )),
])
def test_record_action_admission(tmp_path: Path, subtests: pytest.Subtests, app, labels) -> None:
    run_composed_tests(tmp_path, subtests, app=app, sqlite=labels)
