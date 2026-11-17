"""A triage task's hold: what it may carry, what wakes it, and the event a review hooks."""

from contextlib import contextmanager
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context

from angee.base.models.locks import StaleRevisionError
from angee.work.events import task_woken
from tests.native_work import WorkCase

SNOOZE = """mutation($task: ID!, $until: DateTime, $revision: Int) {
  snooze_task(task: $task, until: $until, expected_revision: $revision) { ok message }
}"""
WAKE = """mutation($task: ID!, $revision: Int) {
  wake_task(task: $task, expected_revision: $revision) { ok message }
}"""
HOLD = """query($id: String!) { project_tasks_by_pk(id: $id) { snoozed_at snoozed_until snoozed_awaiting } }"""


class TaskHoldTests(WorkCase):
    @contextmanager
    def woken(self):
        """Collect every ``task_woken`` as ``(task pk, reason, message pk)``."""

        events = []

        def observe(sender, task, reason, message, **kwargs):
            events.append((task.pk, reason, message.pk if message is not None else None))

        task_woken.connect(observe, weak=False, dispatch_uid="tests.work.holds.observe")
        try:
            yield events
        finally:
            task_woken.disconnect(dispatch_uid="tests.work.holds.observe")

    def hold(self, task):
        return self.Task._base_manager.values_list(
            "snoozed_at", "snoozed_until", "snoozed_by", "snoozed_awaiting", "stage_id",
        ).get(pk=task.pk)

    def snooze(self, task, *args, user=None, **kwargs):
        user = user or self.manager
        with actor_context(user):
            return self.as_user(task, user).snooze(*args, **kwargs)

    def test_a_hold_carries_a_date_an_awaited_user_both_or_neither_and_keeps_triage(self):
        until = timezone.now() + timedelta(days=2)
        for when, awaiting in ((until, None), (None, self.owner), (until, self.owner), (None, None)):
            with self.subTest(until=when, awaiting=awaiting):
                task = self.task("Triage")
                revision = task.revision
                self.snooze(task, when, awaiting=awaiting, expected_revision=revision)
                held_at, held_until, held_by, held_for, stage = self.hold(task)
                self.assertIsNotNone(held_at)
                self.assertEqual((held_until, held_by), (when, self.manager.pk))
                self.assertEqual(held_for, awaiting.pk if awaiting is not None else None)
                self.assertEqual(stage, self.stages["Triage"].pk)
                task.refresh_from_db()
                self.assertEqual(task.revision, revision + 1)

    def test_snooze_takes_the_write_revision_and_triage_checks_of_a_hand_verb(self):
        task = self.task("Triage")
        with self.assertRaises(StaleRevisionError):
            self.snooze(task, expected_revision=task.revision + 1)
        with self.assertRaises(PermissionDenied):
            self.snooze(task, user=self.outsider)
        with self.assertRaisesMessage(ValidationError, "Only a task in triage can be snoozed."):
            self.snooze(self.task("Ready"))
        for when, text in ((timezone.now() - timedelta(minutes=1), "in the future"),
                           (timezone.now().replace(tzinfo=None) + timedelta(days=1), "timezone")):
            with self.subTest(until=when), self.assertRaisesMessage(ValidationError, text):
                self.snooze(task, when)
        self.assertIsNone(self.hold(task)[0])

    def test_resnoozing_keeps_the_hold_start_and_the_same_conditions_change_nothing(self):
        task = self.task("Triage")
        self.snooze(task, awaiting=self.owner)
        started = self.hold(task)[0]
        task.refresh_from_db()
        before = task.revision
        self.snooze(task, awaiting=self.owner)
        task.refresh_from_db()
        self.assertEqual(task.revision, before)
        until = timezone.now() + timedelta(days=1)
        self.snooze(task, until, awaiting=self.owner)
        self.assertEqual(self.hold(task)[:2], (started, until))

    def test_wake_ends_the_hold_in_triage_and_sends_task_woken_once(self):
        task = self.task("Triage")
        self.snooze(task, awaiting=self.owner)
        with self.woken() as events, actor_context(self.manager):
            row = self.as_user(task, self.manager)
            row.wake(expected_revision=row.revision)
            row.wake()
        self.assertEqual(events, [(task.pk, "manual", None)])
        self.assertEqual(self.hold(task), (None, None, None, None, self.stages["Triage"].pk))
        with actor_context(self.manager):
            row = self.as_user(task, self.manager)
            with self.assertRaisesMessage(ValidationError, "Choose due, reply, or manual."):
                row.wake("later")
            with self.assertRaisesMessage(ValidationError, "A reply wake carries its message"):
                row.wake("reply")

    def test_only_the_awaited_users_chatter_wakes_a_hold(self):
        awaited, unawaited = self.task("Triage"), self.task("Triage")
        self.snooze(awaited, awaiting=self.owner)
        self.snooze(unawaited, timezone.now() + timedelta(days=1))
        with self.woken() as events:
            for task in (awaited, unawaited):
                with actor_context(self.manager):
                    self.as_user(task, self.manager).message_post("Which dates do you need?")
            self.assertEqual(events, [])
            with actor_context(self.owner):
                reply = self.as_user(unawaited).message_post("Next week.")
                self.assertEqual(events, [])
                reply = self.as_user(awaited).message_post("Next week.")
        self.assertEqual(events, [(awaited.pk, "reply", reply.pk)])
        self.assertIsNone(self.hold(awaited)[0])
        self.assertIsNotNone(self.hold(unawaited)[0])

    def test_due_holds_wake_row_by_row_with_their_revision(self):
        due, later = self.task("Triage"), self.task("Triage")
        for task in (due, later):
            self.snooze(task, timezone.now() + timedelta(hours=1))
        now = timezone.now() + timedelta(minutes=90)
        with system_context(reason="tests.work.holds.due"):
            self.Task._base_manager.filter(pk=later.pk).update(snoozed_until=now + timedelta(seconds=1))
        due.refresh_from_db()
        before = due.revision
        with self.woken() as events, system_context(reason="tests.work.holds.due"):
            self.assertEqual(self.Task.work.wake_due_snoozes(now=now), 1)
            self.assertEqual(self.Task.work.wake_due_snoozes(now=now), 0)
        self.assertEqual(events, [(due.pk, "due", None)])
        due.refresh_from_db()
        self.assertEqual((due.snoozed_at, due.revision), (None, before + 1))
        self.assertIsNotNone(self.hold(later)[0])

    def test_leaving_triage_ends_a_hold_without_a_wake(self):
        for verb in ("accept", "decline"):
            with self.subTest(verb=verb):
                task = self.task("Triage")
                self.snooze(task, timezone.now() + timedelta(days=1), awaiting=self.owner)
                with self.woken() as events, actor_context(self.manager):
                    row = self.as_user(task, self.manager)
                    row.accept() if verb == "accept" else row.decline("declined")
                self.assertEqual(events, [])
                self.assertEqual(self.hold(task)[:4], (None, None, None, None))

    def test_snooze_and_wake_mutations_hold_undated_and_resume_by_hand(self):
        task = self.task("Triage")
        result = self.graphql(SNOOZE, {"task": task.sqid, "revision": task.revision}, user=self.manager)
        self.assertTrue(result["snooze_task"]["ok"], result)
        hold = self.graphql(HOLD, {"id": task.sqid}, user=self.manager)["project_tasks_by_pk"]
        self.assertIsNotNone(hold["snoozed_at"])
        self.assertEqual((hold["snoozed_until"], hold["snoozed_awaiting"]), (None, None))
        task.refresh_from_db()
        with self.woken() as events:
            result = self.graphql(WAKE, {"task": task.sqid, "revision": task.revision}, user=self.manager)
        self.assertTrue(result["wake_task"]["ok"], result)
        self.assertEqual(events, [(task.pk, "manual", None)])
        self.assertIsNone(self.graphql(HOLD, {"id": task.sqid}, user=self.manager)["project_tasks_by_pk"]["snoozed_at"])
