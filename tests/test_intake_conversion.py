"""A need converts into one triage task, once; a refused conversion leaves it on its project."""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context

from angee.projects.testing.models import Project, Queue, Task
from tests.conftest import Need, create_user


def _project_need(username: str, **queue: Any) -> tuple[Any, Any, Any]:
    """An owner's project carrying one captured need, and a queue to convert it into."""

    owner = create_user(username)
    with actor_context(owner):
        project = Project.objects.create(title="Website")
        need = Need.objects.capture(target=project, body=" The contact form\n bounces ")
    with system_context(reason="tests.intake.conversion"):
        return owner, need, Queue.objects.create(key="DESK", name="Desk", slug="desk", **queue)


def test_converting_a_need_files_one_triage_task_and_retargets_the_need(composed_tables: None) -> None:
    owner, need, queue = _project_need("need-converter")
    project = need.project
    with actor_context(owner):
        task = need.convert_to_task(queue)
        assert need.convert_to_task(queue) == task
    assert (need.task, need.project) == (task, None)
    assert (task.queue, task.stage.category, task.project, task.owner_id) == (queue, "triage", project, None)
    assert (task.title, task.note, task.created_by) == ("The contact form bounces", need.body, owner)
    assert list(Task._base_manager.all()) == [task]


def test_conversion_refuses_a_queue_without_triage_and_an_unsaved_need(composed_tables: None) -> None:
    owner, need, queue = _project_need("need-refused", provision_stages=False)
    project = need.project
    with actor_context(owner):
        with pytest.raises(ValidationError, match="triage"):
            need.convert_to_task(queue)
        with pytest.raises(ValidationError, match="saved need"):
            Need(project=project).convert_to_task(queue)
    need.refresh_from_db()
    assert (need.project, need.task) == (project, None)
    assert not Task._base_manager.exists()
