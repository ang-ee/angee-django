"""Task chatter uses the live queue roster without copying followers."""

from pathlib import Path

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


class TaskAudienceTests(WorkCase):
    def post(self, task, body="Update"):
        with actor_context(self.owner):
            return self.as_user(task).message_post(body)

    def test_roster_creation_note_comments_preferences_and_followers(self):
        self.membership(self.manager, role="owner")
        with actor_context(self.owner):
            task = self.Task.objects.create(title="Created request", queue=self.queue, stage=self.stages["Ready"])
        with actor_context(self.owner):
            creation = task.message_thread_attachment(create=False).thread.messages.order_by("pk").first()
        self.assertIsNotNone(creation)
        expected = {self.member.pk, self.manager.pk, self.moderator.pk}
        self.assertEqual(self.recipients(creation), expected)
        before = self.ThreadFollower._base_manager.count()
        self.assertEqual(self.recipients(self.post(task)), expected)
        self.assertEqual(self.ThreadFollower._base_manager.count(), before)
        with system_context(reason="tests.work.audience_preferences"):
            self.Membership._base_manager.filter(party__person__user=self.member).update(notification_policy="muted")
            self.Membership._base_manager.filter(party__person__user=self.moderator).update(subtype_keys=["created"])
        self.assertEqual(self.recipients(self.post(task)), {self.manager.pk})
        with CaptureQueriesContext(connection) as queries:
            list(task.thread_team().thread_audience())
        self.assertEqual(len(queries), 2, queries.captured_queries)
        self.assertIn('INSERT INTO "rebac_permissionauditevent"', queries[0]["sql"])
        self.assertIn('FROM "spaces_membership"', queries[1]["sql"])

    def test_live_queue_changes_restriction_and_queue_free_followers(self):
        task = self.task()
        self.share(task, self.reader)
        with actor_context(self.reader):
            self.as_user(task, self.reader).message_subscribe()
        self.assertIn(self.member.pk, self.recipients(self.post(task)))
        with actor_context(self.owner):
            self.as_user(task).set_visibility("restricted")
        self.assertEqual(self.recipients(self.post(task)), {self.reader.pk})
        with actor_context(self.owner):
            self.as_user(task).set_visibility("inherited")
        with system_context(reason="tests.work.audience_queue"):
            other = self.Queue.objects.create(name="Other", key="OTHER", owner=self.manager)
        self.membership(self.outsider, queue=other)
        with system_context(reason="tests.work.move_queue"):
            moving = self.Task._base_manager.get(pk=task.pk)
            moving.queue = other
            moving.stage = self.Stage.resolve_default(other)
            moving.save(update_fields=("queue", "stage", "updated_at"))
        self.assertEqual(self.recipients(self.post(task)), {self.outsider.pk, self.reader.pk})
        with system_context(reason="tests.work.no_queue"):
            moving.queue = None
            moving.stage = None
            moving.save(update_fields=("queue", "stage", "updated_at"))
        self.assertEqual(self.recipients(self.post(task)), {self.reader.pk})

    def test_message_post_query_counts_at_two_roster_sizes(self):
        # Measure the public post path, including the recipient read gate.
        with system_context(reason="tests.work.roster_size"):
            self.Membership._base_manager.all().delete()
        task = self.task()
        counts = {}
        for start, size in ((0, 6), (6, 12)):
            for index in range(start, size):
                self.membership(self.person(f"roster-{index}"))
            self.post(task, "Warm reader metadata")
            with CaptureQueriesContext(connection) as queries:
                message = self.post(task, f"Update for {size} members")
            counts[size] = len(queries)
            self.assertEqual(len(self.recipients(message)), size)
            roster = [
                query
                for query in queries
                if "notification_policy" in query["sql"]
                and "spaces_membership" in query["sql"]
                and query["sql"].startswith("SELECT")
            ]
            self.assertEqual(len(roster), 1)
        print(f"Work message_post queries: 6 roster members={counts[6]}, 12 roster members={counts[12]}")


def test_work_task_audience(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_work_task_audience.TaskAudienceTests", app="angee.work")
