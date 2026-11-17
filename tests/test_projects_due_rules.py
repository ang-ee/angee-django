"""Due rules on project tasks and milestones: anchors, precedence, reset, status and repetition."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import override_settings
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

from angee.projects.compose_migrations.projects.task_recurrence_due_rule import (
    applies,
    calendar_rule,
    task_due_rule,
)
from angee.projects.testing.models import Milestone, Project, Task
from angee.scheduling.due import Align, Anchor, Due, DueRule, DueStatus, Offset, Repeat, Term

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
WEEK_AFTER_CREATION = DueRule(terms=[Term(anchor="created", offset=Offset(7, "days"), align=[Align.to_local_day()])])


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
        return Task.objects.create(title="Send the reminder", project=project, **values)


def _reload(row: Any) -> Any:
    return type(row)._base_manager.get(pk=row.pk)


def _slot(day: int, hour: int) -> datetime:
    return datetime(2026, 12, day, hour, tzinfo=UTC)


def test_a_pending_rule_has_no_date_until_its_project_learns_the_anchor() -> None:
    owner = _user("due-owner")
    project = _project(owner)
    task = _task(owner, project, due_rule=BEFORE_FIRST_SLOT)

    stored = _reload(task)
    assert (stored.due_date, stored.due_at) == (None, None)
    assert Due.from_json(stored.due_basis).pending == ("first_slot",)
    assert stored.follows_due_rule()

    with actor_context(owner):
        project.with_actor(owner).set_due_anchor("first_slot", Anchor(at=_slot(10, 19), zone="Europe/Lisbon"))
    stored = _reload(task)
    assert stored.due_at == _slot(8, 19)
    assert stored.due_date == date(2026, 12, 8)
    assert Due.from_json(stored.due_basis).terms[0].origin == Anchor(at=_slot(10, 19), zone="Europe/Lisbon")


def test_a_hand_set_date_wins_keeps_the_rules_basis_and_resets_to_the_rule() -> None:
    owner = _user("due-precedence")
    project = _project(owner)
    with actor_context(owner):
        project.with_actor(owner).set_due_anchor("first_slot", _slot(10, 19))
    task = _task(owner, project, due_rule=BEFORE_FIRST_SLOT)
    assert _reload(task).due_at == _slot(8, 19)

    with actor_context(owner):
        task = Task.objects.get(pk=task.pk)
        task.due_date = date(2026, 12, 1)
        task.save(update_fields=("due_date", "updated_at"))
    hand_set = _reload(task)
    # The hand date wins; the instant was only ever the rule's, and the basis keeps the rule's value.
    assert (hand_set.due_date, hand_set.due_at) == (date(2026, 12, 1), None)
    assert Due.from_json(hand_set.due_basis).at == _slot(8, 19)
    assert not hand_set.follows_due_rule()

    with actor_context(owner):
        project.with_actor(owner).set_due_anchor("first_slot", _slot(12, 19))
    moved = _reload(task)
    assert moved.due_date == date(2026, 12, 1)
    assert Due.from_json(moved.due_basis).at == _slot(10, 19)

    with actor_context(owner):
        Task.objects.get(pk=task.pk).reset_due_to_rule()
    reset = _reload(task)
    assert (reset.due_date, reset.due_at) == (date(2026, 12, 10), _slot(10, 19))
    assert reset.follows_due_rule()


def test_a_task_following_its_rule_moves_with_a_rule_edit() -> None:
    owner = _user("due-edit")
    project = _project(owner)
    with actor_context(owner):
        project.with_actor(owner).set_due_anchor("last_slot", date(2026, 12, 14))  # a Monday
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
    with actor_context(owner):
        project.with_actor(owner).set_due_anchor("last_slot", date(2026, 12, 14))
        phase = Milestone.objects.create(project=project, name="Wrap-up", due_rule=AFTER_LAST_SLOT)
    assert _reload(phase).target_date == date(2026, 12, 21)

    with actor_context(owner):
        Milestone.objects.get(pk=phase.pk).mark_reached()
        project.with_actor(owner).set_due_anchor("last_slot", date(2026, 12, 28))
    # A reached phase's target is history.
    assert _reload(phase).target_date == date(2026, 12, 21)


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

    with actor_context(owner):
        project.with_actor(owner).set_due_anchor("first_slot", date(2026, 12, 1))
        project.with_actor(owner).set_due_anchor("first_slot", None)
    assert _reload(project).due_anchors == {}


def test_due_status_reads_the_rule_while_followed_and_the_hand_set_date_otherwise() -> None:
    owner = _user("due-status")
    project = _project(owner)
    warned = DueRule(terms=[Term(anchor="first_slot", align=[Align.to_local_day()], warn=Offset(3, "days"))])
    with actor_context(owner):
        project.with_actor(owner).set_due_anchor("first_slot", _slot(10, 19))
    task = _reload(_task(owner, project, due_rule=warned))
    assert task.due_status(now=_slot(8, 12)) == DueStatus.WARNING
    assert task.due_status(now=_slot(10, 12)) == DueStatus.DUE

    task.due_date = date(2026, 12, 20)  # hand-set: no warn window of the rule applies
    assert not task.follows_due_rule()
    assert task.due_status(now=_slot(18, 12)) == DueStatus.OK
    assert task.due_status(now=_slot(21, 12)) == DueStatus.OVERDUE
    task.due_date = None
    assert task.due_status(now=_slot(21, 12)) is None


def test_completing_a_repeating_task_creates_its_next_task_once() -> None:
    owner, assignee = _user("repeat-owner"), _user("repeat-assignee")
    project = _project(owner)
    weekly = DueRule(terms=[Term(anchor="set")], repeat=Repeat.from_rrule("FREQ=WEEKLY;BYDAY=FR"))
    task = _task(owner, project, due_date=date(2026, 12, 4), due_rule=weekly, assignee=assignee)

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


def test_a_rule_repeating_from_completion_dates_the_next_task_from_the_work() -> None:
    owner = _user("repeat-completion")
    project = _project(owner)
    after_work = DueRule(
        terms=[Term(anchor="last_done", zone="UTC", offset=Offset(14, "days"), align=[Align.to_local_day()])],
        repeat=Repeat.from_completion(),
    )
    task = _task(owner, project, due_rule=after_work)
    assert Due.from_json(_reload(task).due_basis).pending == ("last_done",)

    with actor_context(owner):
        Task.objects.get(pk=task.pk).complete()
    done_at = _reload(task).done_at
    repetition = Task._base_manager.get(repeated_from=task)
    assert repetition.due_date == done_at.astimezone(UTC).date() + timedelta(days=14)


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


def test_the_recurrence_upgrade_keeps_the_date_and_turns_count_into_until() -> None:
    created = timezone.make_aware(datetime(2026, 9, 1, 9))
    rule, basis = task_due_rule("FREQ=WEEKLY;BYDAY=FR", due_date=date(2026, 9, 4), created_at=created)
    assert rule == {"terms": [{"anchor": "set"}], "repeat": {"kind": "calendar", "rule": "FREQ=WEEKLY;BYDAY=FR"}}
    assert Due.from_json(basis).local_date == date(2026, 9, 4)

    with override_settings(TIME_ZONE="UTC"):
        assert calendar_rule("FREQ=WEEKLY;BYDAY=FR;COUNT=3", date(2026, 9, 4)) == (
            "FREQ=WEEKLY;BYDAY=FR;UNTIL=20260918T000000Z"
        )
    rule, basis = task_due_rule("FREQ=DAILY;COUNT=2", due_date=None, created_at=created)
    assert "COUNT" not in rule["repeat"]["rule"] and "UNTIL=" in rule["repeat"]["rule"]
    assert Due.from_json(basis).pending == ("set",)


def test_the_recurrence_upgrade_applies_while_the_column_exists() -> None:
    class State:
        def __init__(self, fields: tuple[str, ...]) -> None:
            self.models = {("projects", "task"): type("TaskState", (), {"fields": dict.fromkeys(fields)})()}

    assert applies(State(("title", "recurrence")))
    assert not applies(State(("title", "due_rule")))
