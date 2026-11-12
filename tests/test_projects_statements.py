"""Systems of record state projects, milestones and tasks with survivorship."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context

from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.backends import ManualChannelBackend
from angee.messaging.testing.models import Channel
from angee.projects.events import project_status_changed
from angee.projects.testing.models import Milestone, Project, Queue, Task
from angee.provenance.context import changeset
from angee.provenance.schema import RecordHistory
from angee.provenance.statements import SourceKind
from tests.conftest import current_stream, make_integration

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def system_of_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """A channel on the manual protocol asserts as a system of record, as a connector's channel does."""

    monkeypatch.setattr(ManualChannelBackend, "source_kind", SourceKind.SYSTEM_OF_RECORD)


class Source:
    """One system-of-record integration stating rows through one record stream, as a connector does."""

    def __init__(self, slug: str, *, authoritative: bool = False) -> None:
        with system_context(reason="tests.projects.statements.source"):
            channel = make_integration(f"{slug}-source", model=Channel)
            # Rows admit the channel's Integration row, the principal a connector asserts as.
            self.integration = channel.integration_ptr
            self.stream = current_stream(
                channel, "records", kind=StreamKind.RECORD_REPLICA, authoritative=authoritative
            )

    def link(self, row: models.Model) -> Any:
        with system_context(reason="tests.projects.statements.link"):
            return RecordLink.objects.observe(self.stream, f"{row._meta.model_name}:{row.pk}", target=row)

    def state(self, row: Any, aspect: str, **values: Any) -> Any:
        """Assert one aspect through the row's link, as a connector's ``assert_source`` does."""

        link = self.link(row)
        with system_context(reason="tests.projects.statements.state"):
            statement = row.statements_for(aspect).set(
                aspect=aspect, values=values, asserted_by=self.integration, link=link
            )
        row.refresh_from_db()
        return statement


def _person(username: str) -> Any:
    return get_user_model().objects.create_user(username=username, email=f"{username}@example.test")


def _project(owner: Any, **values: Any) -> Any:
    with system_context(reason="tests.projects.statements.project"):
        return Project.objects.create(title="Local project", owner=owner, **values)


def _task(owner: Any, **values: Any) -> Any:
    with system_context(reason="tests.projects.statements.task"):
        return Task.objects.create(**{"title": "Local task", "owner": owner, **values})


def _stance(row: Any, aspect: str, kind: SourceKind) -> Any:
    """The row's current stance on ``aspect`` from the one source of ``kind``, read unscoped."""

    return row.statements.model._base_manager.current().get(subject=row, aspect=aspect, source_kind=kind)


@pytest.fixture
def status_changes() -> Iterator[list[tuple[str, str]]]:
    changes: list[tuple[str, str]] = []

    def record(*, previous_status: str, status: str, **kwargs: Any) -> None:
        changes.append((str(previous_status), str(status)))

    project_status_changed.connect(record, weak=False, dispatch_uid="tests.projects.statements")
    yield changes
    project_status_changed.disconnect(dispatch_uid="tests.projects.statements")


def test_a_system_of_record_states_every_project_aspect(status_changes: list[tuple[str, str]]) -> None:
    """Each aspect applies; the lifecycle changes, stamps and announces through the status owner."""

    owner, lead = _person("stated-project-owner"), _person("stated-project-lead")
    project = _project(owner)
    source = Source("stated-projects")

    source.state(project, "identity", title="Imported project", body="From the ledger")
    source.state(
        project,
        "schedule",
        start_date=date(2026, 1, 1),
        start_date_resolution=Project.ProjectDateResolution.MONTH,
        target_date=date(2026, 6, 30),
        target_date_resolution=Project.ProjectDateResolution.QUARTER,
    )
    source.state(project, "assignment", lead=lead)
    source.state(project, "lifecycle", status=Project.ProjectStatus.PAUSED)

    assert (project.title, project.body, project.lead_id) == ("Imported project", "From the ledger", lead.pk)
    assert (project.start_date, project.start_date_resolution) == (date(2026, 1, 1), "month")
    assert (project.target_date, project.target_date_resolution) == (date(2026, 6, 30), "quarter")
    assert project.status == Project.ProjectStatus.PAUSED and project.status_changed_at is not None
    assert status_changes == [("open", "paused")]
    stance = _stance(project, "lifecycle", SourceKind.SYSTEM_OF_RECORD)
    assert (stance.stated_status, stance.status, stance.applied) == ("paused", "asserted", True)

    closed_at = timezone.now() - timedelta(days=30)
    source.state(project, "lifecycle", status=Project.ProjectStatus.DONE, status_changed_at=closed_at)
    assert (project.status, project.status_changed_at) == (Project.ProjectStatus.DONE, closed_at)
    assert status_changes == [("open", "paused"), ("paused", "done")]
    assert project.with_actor(owner).provenance("status").get().stated_status == "done"


def test_a_system_of_record_states_every_milestone_aspect() -> None:
    """A stated receipt stamps through ``mark_reached`` at the stated time and is never withdrawn."""

    owner = _person("stated-milestone-owner")
    project = _project(owner)
    with system_context(reason="tests.projects.statements.milestone"):
        milestone = Milestone.objects.create(project=project, name="Local phase")
    source = Source("stated-milestones")

    source.state(milestone, "identity", name="Delivery", description="Second instalment")
    source.state(milestone, "schedule", start_date=date(2026, 2, 1), target_date=date(2026, 3, 1))
    reached_at = timezone.now() - timedelta(days=3)
    source.state(milestone, "lifecycle", reached_at=reached_at)

    assert (milestone.name, milestone.description) == ("Delivery", "Second instalment")
    assert (milestone.start_date, milestone.target_date) == (date(2026, 2, 1), date(2026, 3, 1))
    assert (milestone.reached_at, milestone.reached_by_id) == (reached_at, None)

    source.state(milestone, "lifecycle", reached_at=None)
    assert milestone.reached_at == reached_at


def test_a_system_of_record_states_every_task_aspect() -> None:
    """A queue-less task takes each aspect; its lifecycle runs the base verbs with the stated stamps."""

    owner, assignee = _person("stated-task-owner"), _person("stated-task-assignee")
    project = _project(owner)
    with system_context(reason="tests.projects.statements.structure"):
        milestone = Milestone.objects.create(project=project, name="Build")
    parent = _task(owner, project=project, title="Parent")
    task = _task(owner, project=project)
    assert task.queue_id is None
    source = Source("stated-tasks")

    source.state(task, "identity", title="Imported task", note="Body")
    source.state(task, "schedule", due_date=date(2026, 4, 1))
    source.state(task, "priority", priority=Task.TaskPriority.HIGH)
    source.state(task, "assignment", assignee=assignee)
    source.state(task, "structure", project=project, milestone=milestone, parent=parent)
    assert (task.title, task.note, task.due_date, task.priority) == ("Imported task", "Body", date(2026, 4, 1), "high")
    assert (task.assignee_id, task.project_id, task.milestone_id, task.parent_id) == (
        assignee.pk, project.pk, milestone.pk, parent.pk,
    )

    done_at = timezone.now() - timedelta(days=2)
    source.state(task, "lifecycle", status=Task.TaskStatus.DONE, done_at=done_at)
    assert (task.status, task.done_at, task.dropped_at) == (Task.TaskStatus.DONE, done_at, None)

    source.state(task, "lifecycle", status=Task.TaskStatus.DROPPED, dropped_reason=Task.TaskDroppedReason.DECLINED)
    assert (task.status, task.dropped_reason, task.done_at) == (Task.TaskStatus.DROPPED, "declined", None)
    assert task.dropped_at is not None

    source.state(task, "lifecycle", status=Task.TaskStatus.OPEN)
    assert (task.status, task.done_at, task.dropped_at, task.dropped_reason) == (Task.TaskStatus.OPEN, None, None, None)


def test_a_stated_lifecycle_moves_a_queued_task_to_the_stage_that_projects_it() -> None:
    """Work projects status from the stage, so a stated lifecycle enters the matching stage; they never disagree."""

    owner = _person("stated-queue-owner")
    with system_context(reason="tests.projects.statements.queue"):
        queue = Queue.objects.create(name="Imports", key="IMP", owner=owner)
    task = _task(owner, queue=queue)
    source = Source("stated-queue")
    projected = {
        "open": {"triage", "backlog", "unstarted", "started"},
        "done": {"completed"},
        "dropped": {"canceled", "duplicate"},
    }

    def lifecycle() -> tuple[str, str, str | None]:
        task.refresh_from_db()
        assert task.stage is not None and task.stage.category in projected[task.status]
        return task.status, task.stage.category, task.dropped_reason

    assert lifecycle() == ("open", "unstarted", None)
    done_at = timezone.now() - timedelta(hours=5)
    source.state(task, "lifecycle", status=Task.TaskStatus.DONE, done_at=done_at)
    assert lifecycle() == ("done", "completed", None) and task.done_at == done_at
    source.state(task, "lifecycle", status=Task.TaskStatus.DROPPED, dropped_reason=Task.TaskDroppedReason.DECLINED)
    assert lifecycle() == ("dropped", "canceled", "declined")
    source.state(task, "lifecycle", status=Task.TaskStatus.DROPPED, dropped_reason=Task.TaskDroppedReason.DUPLICATE)
    assert lifecycle() == ("dropped", "duplicate", "duplicate")
    source.state(task, "lifecycle", status=Task.TaskStatus.OPEN)
    assert lifecycle() == ("open", "unstarted", None) and task.stage_id == queue.default_stage_id


def test_an_unlinked_source_cannot_state_a_row() -> None:
    """Admission is the row's ``statement_source`` arm: only an integration whose link names the row writes it."""

    owner = _person("unlinked-owner")
    task, other = _task(owner), _task(owner, title="Other")
    source = Source("unlinked")
    link = source.link(other)
    with system_context(reason="tests.projects.statements.unlinked"), pytest.raises(PermissionDenied):
        task.statements_for("identity").set(
            aspect="identity", values={"title": "Foreign"}, asserted_by=source.integration, link=link
        )
    task.refresh_from_db()
    assert task.title == "Local task" and not task.carries_statements()


def test_a_person_edit_on_a_local_row_stays_an_ordinary_deletable_save() -> None:
    """Until a source states a row, a person's edit records no stance and the row deletes natively."""

    owner = _person("local-editor")
    project = _project(owner)
    task = _task(owner, project=project)
    with actor_context(owner):
        milestone = Milestone.objects.create(project=project, name="Phase", target_date=date(2026, 5, 1))
        for model, row, field in ((Project, project, "title"), (Task, task, "title"), (Milestone, milestone, "name")):
            edited = model.objects.get(pk=row.pk)
            setattr(edited, field, "Edited")
            edited.save()
    assert Task._base_manager.get(pk=task.pk).title == "Edited"
    assert not any(row.carries_statements() for row in (project, task, milestone))
    with system_context(reason="tests.projects.statements.delete"):
        Milestone.objects.get(pk=milestone.pk).delete()
        Task.objects.get(pk=task.pk).delete()
        Project.objects.get(pk=project.pk).delete()
    assert not Project._base_manager.filter(pk=project.pk).exists()


def test_a_persons_edit_on_a_stated_row_is_a_stance_ranked_by_stream_authority() -> None:
    """A person outranks a plain stream; an authoritative stream outranks the person; a confirm stays on top."""

    owner = _person("stated-editor")
    task = _task(owner)
    source = Source("stated-reference")
    source.state(task, "identity", title="Remote", note="")

    with actor_context(owner):
        edited = Task.objects.get(pk=task.pk)
        edited.title = "Mine"
        edited.save()
    task.refresh_from_db()
    assert task.title == "Mine" and _stance(task, "identity", SourceKind.PERSON).applied

    source.state(task, "identity", title="Remote, renamed", note="")
    assert task.title == "Mine"

    SyncStream.objects.set_authoritative(source.stream, True)
    task.refresh_from_db()
    assert task.title == "Remote, renamed"
    with actor_context(owner):
        edited = Task.objects.get(pk=task.pk)
        edited.title = "Mine again"
        edited.save()
    task.refresh_from_db()
    assert task.title == "Remote, renamed"
    assert _stance(task, "identity", SourceKind.PERSON).title == "Mine again"

    task.with_actor(owner).confirm(aspect="identity", values={"title": "Decided", "note": ""})
    task.refresh_from_db()
    assert task.title == "Decided"


def test_a_persons_lifecycle_verb_on_a_stated_task_yields_to_an_authoritative_stream() -> None:
    """A person's verb is a lifecycle stance; the authoritative source's stance applies through the stage owner."""

    owner = _person("stated-closer")
    with system_context(reason="tests.projects.statements.queue"):
        queue = Queue.objects.create(name="Replica", key="RPL", owner=owner)
    task = _task(owner, queue=queue)
    source = Source("stated-authority", authoritative=True)
    source.state(task, "lifecycle", status=Task.TaskStatus.OPEN)

    with actor_context(owner):
        Task.objects.get(pk=task.pk).complete()
    task.refresh_from_db()
    assert (task.status, task.stage.category) == ("open", "unstarted")
    assert _stance(task, "lifecycle", SourceKind.PERSON).stated_status == "done"

    SyncStream.objects.set_authoritative(source.stream, False)
    task.refresh_from_db()
    assert (task.status, task.stage.category) == ("done", "completed")

    # Confirming the source's stance takes its stated status, held beside the stance's own.
    confirmed = _stance(task, "lifecycle", SourceKind.SYSTEM_OF_RECORD).with_actor(owner).confirm()
    task.refresh_from_db()
    assert (confirmed.stated_status, confirmed.is_confirmed) == ("open", True)
    assert (task.status, task.stage.category) == ("open", "unstarted")


def test_a_tombstone_withdraws_the_sources_statements_and_the_persons_stance_applies() -> None:
    """Withdrawal keeps operational values a source alone stated and lets the surviving stance apply."""

    owner = _person("tombstone-owner")
    project = _project(owner)
    task = _task(owner, project=project)
    source = Source("tombstone", authoritative=True)
    source.state(project, "identity", title="Remote project", body="")
    source.state(task, "identity", title="Remote task", note="")
    with actor_context(owner):
        edited = Task.objects.get(pk=task.pk)
        edited.title = "Local task title"
        edited.save()
    task.refresh_from_db()
    assert task.title == "Remote task"

    with system_context(reason="tests.projects.statements.tombstone"):
        for row in (project, task):
            link = RecordLink.objects.tombstone(source.link(row))
            assert link.withdraw_statements() == 1
    project.refresh_from_db()
    task.refresh_from_db()
    assert project.title == "Remote project"
    assert _stance(project, "identity", SourceKind.SYSTEM_OF_RECORD).status == "withdrawn"
    assert task.title == "Local task title"
    assert _stance(task, "identity", SourceKind.PERSON).applied


@pytest.mark.django_db(transaction=True)
def test_record_history_projects_a_stated_status_under_its_field_name() -> None:
    """The stance keeps its own ``status``; history reads the stated one from its ``stated_status`` column."""

    owner = _person("history-reader")
    source = Source("history")
    with changeset(fresh=True, change_reason="Import task"):
        task = _task(owner)
        source.state(task, "lifecycle", status=Task.TaskStatus.DONE)
    lifecycle = next(
        statement
        for entry in RecordHistory(task, actor=owner).page(25, 0).entries
        for statement in entry.statements
        if statement.aspect == "lifecycle"
    )
    assert (lifecycle.values["status"], str(lifecycle.status)) == ("done", "asserted")
