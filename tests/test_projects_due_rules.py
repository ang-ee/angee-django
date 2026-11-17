"""Due rules on project tasks and milestones: anchors, holds, reset, status and repetition."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.projects.compose_migrations.projects.task_recurrence_due_rule import calendar_rule, task_due_rule
from angee.projects.testing.models import Milestone, Project, Task
from angee.scheduling.due import Align, Anchor, Due, DueRule, DueStatus, Offset, Repeat, Term
from tests.test_projects_statements import Source
from tests.test_projects_statements import system_of_record as system_of_record  # noqa: F401 -- autouse

User = get_user_model()
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("activity_catalog")]

BEFORE_FIRST_SLOT = DueRule(terms=[Term(anchor="first_slot", offset=Offset(-48, "hours"))])
AFTER_LAST_SLOT = DueRule(
    terms=[
        Term(
            anchor="last_slot",
            align=[
                Align.to_next_weekday("MO", strict=True),
                Align.to_local_day(),
            ],
        )
    ]
)
ON_LAST_SLOT = DueRule(terms=[Term(anchor="last_slot", align=[Align.to_local_day()])])
WEEK_AFTER_CREATION = DueRule(terms=[Term(anchor="created", offset=Offset(7, "days"), align=[Align.to_local_day()])])
WEEKLY = DueRule(terms=[Term(anchor="set")], repeat=Repeat.from_rrule("FREQ=WEEKLY;BYDAY=FR"))
AFTER_WORK = DueRule(
    terms=[Term(anchor="last_done", zone="UTC", offset=Offset(14, "days"), align=[Align.to_local_day()])],
    repeat=Repeat.from_completion(),
)


def _user(name: str) -> Any:
    with system_context(reason="tests.projects.due_rules.user"):
        return User.objects.create_user(username=name, email=f"{name}@example.test")


def _grant(resource: Any, relation: str, user: Any) -> None:
    with system_context(reason="tests.projects.due_rules.grant"):
        write_relationships([RelationshipTuple(to_object_ref(resource), relation, to_subject_ref(user))])


def _project(owner: Any, **values: Any) -> Any:
    with actor_context(owner):
        return Project.objects.create(title="Spring showcase", **values)


def _task(owner: Any, project: Any, **values: Any) -> Any:
    with actor_context(owner):
        return Task.objects.create(**{"title": "Send the reminder", "project": project, **values})


def _reload(row: Any) -> Any:
    return type(row)._base_manager.get(pk=row.pk)


def _anchor(owner: Any, project: Any, name: str, value: Any) -> None:
    with actor_context(owner):
        Project.objects.get(pk=project.pk).set_due_anchor(name, value)


def _hand(owner: Any, task: Any, day: date | None) -> None:
    with actor_context(owner):
        row = Task.objects.get(pk=task.pk)
        row.due_date = day
        row.save(update_fields=("due_date", "updated_at"))


def _slot(day: int, hour: int) -> datetime:
    return datetime(2026, 12, day, hour, tzinfo=UTC)


def test_a_pending_rule_has_no_date_until_its_project_learns_the_anchor() -> None:
    owner = _user("due-owner")
    project = _project(owner)
    task = _task(owner, project, due_rule=BEFORE_FIRST_SLOT)

    stored = _reload(task)
    assert (stored.due_date, stored.due_at) == (None, None)
    assert stored.rule_due().pending == ("first_slot",)
    assert stored.follows_due_rule()

    _anchor(owner, project, "first_slot", Anchor(at=_slot(10, 19), zone="Europe/Lisbon"))
    stored = _reload(task)
    assert stored.due_at == _slot(8, 19)
    assert stored.due_date == date(2026, 12, 8)
    assert stored.rule_due().terms[0].origin == Anchor(at=_slot(10, 19), zone="Europe/Lisbon")


def test_a_hand_set_date_holds_keeps_the_rules_basis_and_resets_to_the_rule() -> None:
    owner = _user("due-precedence")
    project = _project(owner)
    _anchor(owner, project, "first_slot", _slot(10, 19))
    task = _task(owner, project, due_rule=BEFORE_FIRST_SLOT)
    assert _reload(task).due_at == _slot(8, 19)

    _hand(owner, task, date(2026, 12, 1))
    held = _reload(task)
    # The hand date holds; the instant was only ever the rule's, and the basis keeps the rule's value.
    assert (held.due_date, held.due_at, held.due_held()) == (date(2026, 12, 1), None, True)
    assert held.rule_due().at == _slot(8, 19)
    assert not held.follows_due_rule()

    _anchor(owner, project, "first_slot", _slot(12, 19))
    moved = _reload(task)
    assert moved.due_date == date(2026, 12, 1)
    assert moved.rule_due().at == _slot(10, 19)

    with actor_context(owner):
        Task.objects.get(pk=task.pk).reset_due_to_rule()
    reset = _reload(task)
    assert (reset.due_date, reset.due_at, reset.due_held()) == (date(2026, 12, 10), _slot(10, 19), False)
    assert reset.follows_due_rule()


def test_a_hand_date_equal_to_the_rules_value_still_holds_when_the_anchor_moves() -> None:
    owner = _user("due-equal")
    project = _project(owner)
    _anchor(owner, project, "last_slot", date(2026, 12, 14))
    task = _task(owner, project, due_rule=ON_LAST_SLOT)
    _hand(owner, task, date(2026, 12, 14))  # a person types the rule's own date deliberately
    _anchor(owner, project, "last_slot", date(2026, 12, 28))
    assert _reload(task).due_date == date(2026, 12, 14)


def test_a_stated_date_holds_even_at_the_rules_value_and_reset_releases_it() -> None:
    owner = _user("due-stated")
    project = _project(owner)
    _anchor(owner, project, "last_slot", date(2026, 12, 14))
    task = _task(owner, project, due_rule=ON_LAST_SLOT)
    source = Source("tracker")
    source.state(_reload(task), "schedule", due_date=date(2026, 12, 14))
    _anchor(owner, project, "last_slot", date(2026, 12, 28))
    stated = _reload(task)
    assert (stated.due_date, stated.due_held()) == (date(2026, 12, 14), True)

    with actor_context(owner):
        Task.objects.get(pk=task.pk).reset_due_to_rule()
    assert (_reload(task).due_date, _reload(task).due_held()) == (date(2026, 12, 28), False)


def test_an_insert_with_a_date_holds_and_one_without_follows() -> None:
    owner = _user("due-insert")
    project = _project(owner)
    _anchor(owner, project, "last_slot", date(2026, 12, 14))
    assert _reload(_task(owner, project, due_rule=ON_LAST_SLOT, due_date=date(2026, 12, 2))).due_held()
    following = _reload(_task(owner, project, due_rule=ON_LAST_SLOT))
    assert (following.due_date, following.due_held()) == (date(2026, 12, 14), False)


def test_a_task_following_its_rule_moves_with_a_rule_edit() -> None:
    owner = _user("due-edit")
    project = _project(owner)
    _anchor(owner, project, "last_slot", date(2026, 12, 14))  # a Monday
    task = _task(owner, project, due_rule=AFTER_LAST_SLOT)
    assert _reload(task).due_date == date(2026, 12, 21)

    with actor_context(owner):
        task = Task.objects.get(pk=task.pk)
        task.due_rule = DueRule(terms=[Term(anchor="last_slot", offset=Offset(2, "days"))])
        task.save(update_fields=("due_rule", "updated_at"))
    assert _reload(task).due_date == date(2026, 12, 16)


@override_settings(TIME_ZONE="UTC")
def test_a_task_about_no_project_counts_from_its_own_creation() -> None:
    owner = _user("due-standalone")
    with actor_context(owner):
        task = Task.objects.create(title="File the notes", owner=owner, due_rule=WEEK_AFTER_CREATION)
    stored = _reload(task)
    assert stored.due_date == stored.created_at.date() + timedelta(days=7)
    assert stored.due_at is None


def test_a_milestone_target_follows_its_rule_until_it_is_reached() -> None:
    owner = _user("due-milestone")
    project = _project(owner)
    _anchor(owner, project, "last_slot", date(2026, 12, 14))
    with actor_context(owner):
        phase = Milestone.objects.create(project=project, name="Wrap-up", due_rule=AFTER_LAST_SLOT)
    assert _reload(phase).target_date == date(2026, 12, 21)

    with actor_context(owner):
        Milestone.objects.get(pk=phase.pk).mark_reached()
    _anchor(owner, project, "last_slot", date(2026, 12, 28))
    # A reached phase's target is history: anchors leave it and a reset is refused.
    assert _reload(phase).target_date == date(2026, 12, 21)
    with actor_context(owner), pytest.raises(ValidationError, match="history"):
        Milestone.objects.get(pk=phase.pk).reset_due_to_rule()


def test_a_closed_task_keeps_its_date_and_refuses_a_reset() -> None:
    owner = _user("due-closed")
    project = _project(owner)
    _anchor(owner, project, "last_slot", date(2026, 12, 14))
    task = _task(owner, project, due_rule=ON_LAST_SLOT)
    with actor_context(owner):
        Task.objects.get(pk=task.pk).drop("obsolete")
    _anchor(owner, project, "last_slot", date(2026, 12, 28))
    assert _reload(task).due_date == date(2026, 12, 14)
    with actor_context(owner), pytest.raises(ValidationError, match="history"):
        Task.objects.get(pk=task.pk).reset_due_to_rule()


def test_a_rule_over_counters_is_refused_and_one_failing_row_never_blocks_the_anchors() -> None:
    owner = _user("due-poison")
    project = _project(owner)
    counter = {"terms": [{"anchor": "first_slot", "counter": "usage_hours", "offset": {"amount": 50}}]}
    with pytest.raises(ValidationError, match="counters"):
        _task(owner, project, due_rule=counter)
    fine = _task(owner, project, title="Fine", due_rule=ON_LAST_SLOT)
    far = _task(
        owner, project, title="Far", due_rule=DueRule(terms=[Term(anchor="last_slot", offset=Offset(999, "years"))])
    )

    _anchor(owner, project, "last_slot", date(9500, 1, 4))  # 999 years later leaves the calendar
    assert _reload(fine).due_date == date(9500, 1, 4)
    failed = _reload(far)
    assert failed.due_date is None and "unresolved" in failed.due_basis and failed.rule_due() is None


def test_set_due_anchor_refuses_built_in_names_templates_and_readers() -> None:
    owner, reader = _user("anchor-owner"), _user("anchor-reader")
    project = _project(owner)
    _grant(project, "reader", reader)
    with actor_context(owner):
        for name in ("created", "set", "", "x" * 65):
            with pytest.raises(ValidationError):
                project.with_actor(owner).set_due_anchor(name, date(2026, 12, 1))
    with actor_context(reader), pytest.raises(PermissionDenied):
        Project.objects.get(pk=project.pk).set_due_anchor("first_slot", date(2026, 12, 1))
    template = _project(owner, is_template=True)
    with actor_context(owner), pytest.raises(ValidationError, match="template"):
        template.with_actor(owner).set_due_anchor("first_slot", date(2026, 12, 1))

    _anchor(owner, project, "first_slot", date(2026, 12, 1))
    _anchor(owner, project, "first_slot", None)
    assert _reload(project).due_anchors == {}


def test_set_due_anchor_reads_the_project_once_however_many_rows_follow() -> None:
    owner = _user("anchor-cost")
    project = _project(owner)

    def project_reads(rows: int) -> int:
        for index in range(rows - Task._base_manager.filter(project=project).count()):
            _task(owner, project, title=f"Step {index}", due_rule=ON_LAST_SLOT)
        with CaptureQueriesContext(connection) as queries:
            _anchor(owner, project, "last_slot", date(2026, 12, rows))
        table = Project._meta.db_table
        return sum(
            1
            for query in queries.captured_queries
            if query["sql"].startswith("SELECT") and f'FROM "{table}"' in query["sql"]
        )

    assert project_reads(4) == project_reads(12)


def test_due_status_reads_the_rule_while_followed_and_the_hand_set_date_otherwise() -> None:
    owner = _user("due-status")
    project = _project(owner)
    warned = DueRule(terms=[Term(anchor="first_slot", align=[Align.to_local_day()], warn=Offset(3, "days"))])
    _anchor(owner, project, "first_slot", _slot(10, 19))
    task = _task(owner, project, due_rule=warned)
    assert _reload(task).due_status(now=_slot(8, 12)) == DueStatus.WARNING
    assert _reload(task).due_status(now=_slot(10, 12)) == DueStatus.DUE

    _hand(owner, task, date(2026, 12, 20))  # held: no warn window of the rule applies
    held = _reload(task)
    assert held.due_status(now=_slot(18, 12)) == DueStatus.OK
    assert held.due_status(now=_slot(21, 12)) == DueStatus.OVERDUE
    _hand(owner, task, None)
    assert _reload(task).due_status(now=_slot(21, 12)) is None


def test_completing_a_repeating_task_creates_its_next_task_once() -> None:
    owner, assignee = _user("repeat-owner"), _user("repeat-assignee")
    project = _project(owner)
    task = _task(owner, project, due_date=date(2026, 12, 4), due_rule=WEEKLY, assignee=assignee)
    # The test host files tasks in a queue: completion runs through the stage projection, not the verb's save.
    assert _reload(task).queue_id is not None

    # The assignee writes the task but not the project: the repetition is the rule's system write.
    with actor_context(assignee):
        Task.objects.get(pk=task.pk).complete()
    task = _reload(task)
    repetition = Task._base_manager.get(repeated_from=task)
    assert (repetition.status, repetition.due_date) == ("open", date(2026, 12, 11))
    assert (repetition.project_id, repetition.assignee_id, repetition.owner_id) == (project.pk, assignee.pk, owner.pk)
    assert repetition.due_rule == task.due_rule and repetition.follows_due_rule()
    assert repetition.sort_order != task.sort_order

    with actor_context(owner):
        Task.objects.get(pk=task.pk).reopen()
        Task.objects.get(pk=task.pk).complete()
        Task.objects.get(pk=task.pk).complete()
    assert Task._base_manager.filter(repeated_from=task).count() == 1

    with actor_context(owner):
        Task.objects.get(pk=repetition.pk).complete()
    assert Task._base_manager.get(repeated_from=repetition).due_date == date(2026, 12, 18)


def test_a_rule_repeating_from_completion_dates_the_next_task_from_the_stored_completion() -> None:
    owner = _user("repeat-completion")
    project = _project(owner)
    task = _task(owner, project, due_rule=AFTER_WORK)
    assert _reload(task).rule_due().pending == ("last_done",)

    with actor_context(owner):
        Task.objects.get(pk=task.pk).complete()
    done_at = _reload(task).done_at
    repetition = Task._base_manager.get(repeated_from=task)
    due = done_at.astimezone(UTC).date() + timedelta(days=14)
    assert repetition.due_date == due

    # Reopening the completed task and moving an anchor never moves its repetition.
    with actor_context(owner):
        Task.objects.get(pk=task.pk).reopen()
    _anchor(owner, project, "unrelated", date(2026, 12, 1))
    assert _reload(repetition).due_date == due
    with actor_context(owner):
        Task.objects.get(pk=task.pk).complete()
    assert Task._base_manager.filter(repeated_from=task).count() == 1


def test_a_rule_that_does_not_repeat_or_has_ended_creates_nothing() -> None:
    owner = _user("repeat-none")
    project = _project(owner)
    ended = DueRule(terms=[Term(anchor="set")], repeat=Repeat.from_rrule("FREQ=WEEKLY;UNTIL=20261205T000000Z"))
    once = _task(owner, project, due_date=date(2026, 12, 4), due_rule=DueRule(terms=[Term(anchor="set")]))
    last = _task(owner, project, due_date=date(2026, 12, 4), due_rule=ended)
    hand = _task(owner, project, due_date=date(2026, 12, 4))
    with actor_context(owner):
        for row in (once, last, hand):
            Task.objects.get(pk=row.pk).complete()
    assert not Task._base_manager.filter(repeated_from__in=(once, last, hand)).exists()


def test_a_repetition_stays_out_of_closed_projects_and_parents_and_leaves_a_reached_phase() -> None:
    owner = _user("repeat-gaps")
    closed = _project(owner)
    in_closed = _task(owner, closed, due_date=date(2026, 12, 4), due_rule=WEEKLY)
    live = _project(owner)
    parent = _task(owner, live, title="Series")
    child = _task(owner, live, parent=parent, due_date=date(2026, 12, 4), due_rule=WEEKLY)
    with actor_context(owner):
        phase = Milestone.objects.create(project=live, name="Launch")
    phased = _task(owner, live, milestone=phase, due_date=date(2026, 12, 4), due_rule=WEEKLY)
    with actor_context(owner):
        Project.objects.get(pk=closed.pk).complete()
        Task.objects.get(pk=parent.pk).complete()
        Milestone.objects.get(pk=phase.pk).mark_reached()
        for row in (in_closed, child, phased):
            Task.objects.get(pk=row.pk).complete()
    assert not Task._base_manager.filter(repeated_from__in=(in_closed, child)).exists()
    assert Task._base_manager.get(repeated_from=phased).milestone_id is None


def test_a_failed_repetition_keeps_the_completion_and_is_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = _user("repeat-failure")
    project = _project(owner)
    task = _task(owner, project, due_date=date(2026, 12, 4), due_rule=WEEKLY)

    def refuse(row: Any) -> dict[str, Any]:
        raise ValidationError("The next task cannot be created.")

    monkeypatch.setattr(Task, "repetition_values", refuse)
    with actor_context(owner), caplog.at_level(logging.ERROR, logger="angee.projects.models"):
        Task.objects.get(pk=task.pk).complete()
    assert _reload(task).status == "done"
    assert not Task._base_manager.filter(repeated_from=task).exists()
    assert "repetition could not be created" in caplog.text


def test_the_recurrence_upgrade_keeps_the_date_and_turns_count_into_until() -> None:
    created = timezone.make_aware(datetime(2026, 9, 1, 9))
    moved = task_due_rule("FREQ=WEEKLY;BYDAY=FR", due_date=date(2026, 9, 4), created_at=created)
    assert moved is not None
    rule, basis = moved
    assert rule == {"terms": [{"anchor": "set"}], "repeat": {"kind": "calendar", "rule": "FREQ=WEEKLY;BYDAY=FR"}}
    assert Due.from_json(basis).local_date == date(2026, 9, 4)

    with override_settings(TIME_ZONE="UTC"):
        # The due day is an occurrence: three points end on the third Friday.
        assert calendar_rule("FREQ=WEEKLY;BYDAY=FR;COUNT=3", date(2026, 9, 4)) == (
            "FREQ=WEEKLY;BYDAY=FR;UNTIL=20260918T000000Z"
        )
        # A Thursday due day is the first of three points (RFC 5545 counts DTSTART).
        assert calendar_rule("FREQ=WEEKLY;BYDAY=FR;COUNT=3", date(2026, 9, 3)) == (
            "FREQ=WEEKLY;BYDAY=FR;UNTIL=20260911T000000Z"
        )
        assert calendar_rule("FREQ=WEEKLY;COUNT=1", date(2026, 9, 3)) is None
    moved = task_due_rule("FREQ=DAILY;COUNT=2", due_date=None, created_at=created)
    assert moved is not None
    rule, basis = moved
    assert "COUNT" not in rule["repeat"]["rule"] and "UNTIL=" in rule["repeat"]["rule"]
    assert Due.from_json(basis).pending == ("set",)
    with pytest.raises((ValidationError, ValueError)):
        task_due_rule("FREQ=SOMETIMES", due_date=None, created_at=created)

