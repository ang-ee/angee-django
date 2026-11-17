"""Work's task selections and triage factory, contributed to projects.Task as ``Task.work``."""

from datetime import timedelta

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context

from angee.messaging.testing.models import Thread
from angee.projects.testing.models import Queue, Task
from tests.conftest import create_user

pytestmark = pytest.mark.django_db


def test_task_keeps_objects_as_default_manager_beside_the_work_manager() -> None:
    assert Task._default_manager.name == "objects"
    assert Task._base_manager.name == "_angee_base"
    assert Task.work.name == "work"
    # The work manager extends the projects task manager rather than replacing it.
    assert callable(Task.work.schedule)


def test_capture_requires_a_trusted_system_context_and_files_ownerless_triage() -> None:
    owner = create_user("work-scopes-owner")
    with system_context(reason="tests.work.scopes.queue"):
        queue = Queue.objects.create(key="SCOPES", name="Scopes", slug="scopes", triage_enabled=True)
    with actor_context(owner), pytest.raises(PermissionDenied):
        Task.work.capture_triage(queue=queue, title="Captured", note="")
    with system_context(reason="tests.work.scopes.capture"):
        task = Task.work.capture_triage(
            queue=queue, title="  Captured   need  ", note="Evidence", created_by_id=owner.pk
        )
    assert (task.title, task.note, task.stage.category) == ("Captured need", "Evidence", "triage")
    assert (task.owner_id, task.created_by_id) == (None, owner.pk)


def test_an_awaited_reply_wakes_only_the_task_owning_that_chatter_thread() -> None:
    now = timezone.now()
    with system_context(reason="tests.work.scopes.chatter"):
        snoozer, replier = create_user("work-scopes-snoozer"), create_user("work-scopes-replier")
        tasks = []
        for title in ("Chatter", "Elsewhere"):
            task = Task(title=title, snoozed_at=now, snoozed_by=snoozer, snoozed_awaiting=replier)
            task.sudo(reason="tests.work.scopes.chatter.create").save(ownerless=True)
            tasks.append(task)
        thread = Thread.objects.create(modality=Thread.Modality.GROUP)
        apps.get_model("messaging", "ThreadAttachment")._base_manager.create(
            thread=thread,
            content_type=ContentType.objects.get_for_model(Task),
            object_id=tasks[0].pk,
            role="chatter",
        )
        message_model = apps.get_model("messaging", "Message")
        by_snoozer, by_replier = (
            message_model._base_manager.create(thread=thread, created_by=author) for author in (snoozer, replier)
        )
        assert Task.work.wake_on_chatter_reply(message_model(thread=None, created_by=replier)) == 0
        assert Task.work.wake_on_chatter_reply(by_snoozer) == 0
        assert Task.work.wake_on_chatter_reply(by_replier) == 1
        assert Task.work.wake_on_chatter_reply(by_replier) == 0
    woken, other = (Task._base_manager.get(pk=task.pk) for task in tasks)
    assert (woken.snoozed_at, woken.snoozed_by_id, woken.snoozed_awaiting_id) == (None, None, None)
    assert other.snoozed_at is not None


def test_wake_due_snoozes_honours_the_inclusive_wake_instant() -> None:
    now = timezone.now()
    with system_context(reason="tests.work.scopes.due"):
        snoozer = create_user("work-scopes-due")
        due, later = (
            Task(title=title, snoozed_at=now, snoozed_until=until, snoozed_by=snoozer)
            for title, until in (("Due", now), ("Later", now + timedelta(seconds=1)))
        )
        for task in (due, later):
            task.sudo(reason="tests.work.scopes.due.create").save(ownerless=True)
        assert Task.work.wake_due_snoozes(now=now) == 1
    assert Task._base_manager.values_list("snoozed_at", "snoozed_until").get(pk=due.pk) == (None, None)
    assert Task._base_manager.get(pk=later.pk).snoozed_at is not None
