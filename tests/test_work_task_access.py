"""Queue inheritance, comment access and fragment scope on composed work tasks."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import EmptyResultSet
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref
from rebac.backends import backend
from rebac.backends.local_query import LocalQueryScope
from rebac.conf import app_settings
from rebac.models import active_relationship_model
from rebac.schema.parser import parse_zed

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


def withhold_assignees(case):
    """Contribute a test-only queue exclusion through the native fragment seam."""
    original = backend().schema()
    schema = deepcopy(original)
    fragment = parse_zed("""definition work/queue {
        relation excluded: auth/user // rebac:field=tasks__assignee
        permission withheld = excluded
    }""").definitions[0]
    schema.definitions = [
        definition.extend(relations=fragment.relations, permission_arms=fragment.permissions)
        if definition.resource_type == "work/queue"
        else definition
        for definition in schema.definitions
    ]
    backend().set_schema(schema)
    case.addCleanup(backend().set_schema, original)


class TaskAccessTests(WorkCase):
    def test_restricted_and_null_stage_remove_queue_access(self):
        task = self.task(assignee=self.assignee)
        self.share(task, self.reader)
        for user in (self.member, self.viewer, self.moderator):
            self.assertTrue(self.scoped(task, user))
        with actor_context(self.owner):
            self.as_user(task).set_visibility("restricted")
        for user in (self.member, self.viewer, self.moderator):
            for permission in ("read", "write", "share", "comment"):
                self.assertFalse(self.scoped(task, user, permission))
        for user in (self.owner, self.assignee, self.reader):
            self.assertTrue(self.scoped(task, user))
        for user in (self.owner, self.assignee):
            self.assertTrue(self.scoped(task, user, "write"))
        self.Task._base_manager.filter(pk=task.pk).update(stage=None, visibility="inherited")
        self.assertFalse(self.scoped(task, self.member))

    def test_stage_position_controls_order(self):
        later = self.task("Doing", title="A")
        earlier = self.task("Ready", title="Z")
        self.assertEqual(
            list(self.Task._base_manager.order_by("stage__position").values_list("pk", flat=True)),
            [earlier.pk, later.pk],
        )

    def test_member_comments_and_moderator_assigns(self):
        task = self.task()
        self.assertTrue(self.scoped(task, self.member, "comment"))
        self.assertFalse(self.scoped(task, self.member, "write"))
        self.assertFalse(self.scoped(task, self.member, "share"))
        self.assertFalse(self.scoped(task, self.viewer, "comment"))
        with actor_context(self.member):
            record = self.as_user(task, self.member)
            own = record.message_post("Member comment")
            record.message_update_content(own.with_actor(self.member), body="Edited comment")
        with actor_context(self.owner):
            other = self.as_user(task).message_post("Owner comment")
        with actor_context(self.member):
            with self.assertRaises((ValueError, PermissionDenied)):
                record.message_unlink(other.with_actor(self.member))
            record.message_unlink(own.with_actor(self.member))
        with actor_context(self.moderator):
            self.as_user(task, self.moderator).message_unlink(other.with_actor(self.moderator))
            assigned = self.as_user(task, self.moderator)
            assigned.assignee = self.assignee
            assigned.save(update_fields=("assignee", "updated_at"))
        with actor_context(self.member), self.assertRaises(PermissionDenied):
            assigned = self.as_user(task, self.member)
            assigned.assignee = self.reader
            assigned.save(update_fields=("assignee", "updated_at"))
        task.refresh_from_db()
        self.assertEqual(task.assignee_id, self.assignee.pk)

    def test_queue_withheld_fragment_keeps_the_manager_exception(self):
        withhold_assignees(self)
        self.task(assignee=self.member)
        self.task(assignee=self.moderator)
        ordinary = self.task()
        for row in (self.queue, self.stages["Ready"], ordinary):
            self.assertFalse(self.scoped(row, self.member))
            self.assertTrue(self.scoped(row, self.moderator))

    def test_all_permissions_compile_within_frame_limit(self):
        deepest = 0
        original = LocalQueryScope.permission

        def traced(scope, resource_type, action, model, identity, seen, *args, **kwargs):
            nonlocal deepest
            deepest = max(deepest, len(seen) + 1)
            return original(scope, resource_type, action, model, identity, seen, *args, **kwargs)

        with patch.object(LocalQueryScope, "permission", traced):
            for model in (self.Task, self.Queue, self.Stage, self.Cycle, self.Project, self.Milestone, self.Link):
                definition = backend().schema().get_definition(model._meta.rebac_resource_type)
                for permission in definition.permissions:
                    predicate = LocalQueryScope(backend(), to_subject_ref(self.member), "default").predicate(
                        model,
                        permission.name,
                        definition.resource_type,
                    )
                    try:
                        sql = model._base_manager.filter(predicate).query.sql_with_params()[0]
                    except EmptyResultSet:
                        # A declared nil permission compiles to an empty predicate.
                        continue
                    self.assertIn("SELECT", sql)
        self.assertLessEqual(deepest, app_settings.REBAC_DEPTH_LIMIT)
        print(f"Work SQL compilation ({self.storage}): maximum {deepest}/{app_settings.REBAC_DEPTH_LIMIT} frames")


class TaskAccessDenormalizedTests(TaskAccessTests):
    storage = "denormalized"


class PublicQueueTests(WorkCase):
    def test_public_creation_and_withheld_outsider(self):
        """Requires the spaces owner's public visibility filtered constant."""
        with system_context(reason="tests.work.public_queue"):
            queue = self.Queue.objects.create(
                name="Public requests", key="PUB", owner=self.manager, visibility="public"
            )
        task = self.task(queue=queue, stage=None)
        self.assertTrue(self.scoped(task, self.outsider))
        withhold_assignees(self)
        self.task(queue=queue, stage=None, assignee=self.outsider)
        self.assertFalse(self.scoped(task, self.outsider))


class QueuePermissionsTests(WorkCase):
    def test_queue_permissions_batch_for_members_and_hide_from_outsiders(self):
        query = "{ work_queues(order_by: [{key: asc}]) { id permissions } }"
        query_counts = {}
        for total in (1, 10):
            with system_context(reason="tests.work.queue_permissions"):
                for index in range(self.Queue._base_manager.count(), total):
                    queue = self.Queue.objects.create(
                        name=f"Queue {index}", key=f"Q{index}", owner=self.manager, provision_stages=False
                    )
                    self.membership(self.member, queue=queue)
                    self.membership(self.moderator, queue=queue, role="moderator")
            for user, expected in (
                (self.member, ("read",)),
                (self.moderator, ("read", "write")),
                (self.manager, ("read", "share", "write")),
            ):
                # Warm schema and permission caches independently of list size.
                self.graphql(query, {}, user=user)
                with CaptureQueriesContext(connection) as captured:
                    rows = self.graphql(query, {}, user=user)["work_queues"]
                self.assertEqual(len(rows), total)
                self.assertEqual({tuple(row["permissions"]) for row in rows}, {expected})
                query_counts[user.pk, total] = len(captured)
            self.assertEqual(self.graphql(query, {}, user=self.outsider)["work_queues"], [])
        for user in (self.member, self.moderator, self.manager):
            self.assertGreater(query_counts[user.pk, 1], 0)
            self.assertEqual(query_counts[user.pk, 1], query_counts[user.pk, 10], query_counts)

    def test_public_queue_permissions_batch_for_outsiders(self):
        query = "{ work_queues { id permissions } }"
        counts = []
        with system_context(reason="tests.work.public_queue_permissions"):
            self.Queue.objects.filter(pk=self.queue.pk).update(visibility="public")
        for total in (1, 10):
            with system_context(reason="tests.work.public_queue_permissions"):
                for index in range(self.Queue._base_manager.count(), total):
                    self.Queue.objects.create(
                        name=f"Public queue {index}", key=f"P{index}", owner=self.manager,
                        visibility="public", provision_stages=False,
                    )
            self.graphql(query, {}, user=self.outsider)
            with CaptureQueriesContext(connection) as captured:
                rows = self.graphql(query, {}, user=self.outsider)["work_queues"]
            self.assertEqual(len(rows), total)
            self.assertEqual({tuple(row["permissions"]) for row in rows}, {("read",)})
            counts.append(len(captured))
        self.assertGreater(counts[0], 0)
        self.assertEqual(*counts)


class SchemaImportTests(WorkCase):
    def test_projects_schema_consumes_work_declarations(self):
        from angee.projects import schema

        self.assertIs(schema.Task, self.Task)
        self.assertIn("stage__position", self.Task.hasura_sortable_fields)
        self.assertIn("active_stage", schema._MILESTONE_EXTENSION_PUBLIC_ID_FIELDS)


class DuplicateLinkTests(WorkCase):
    def test_duplicate_merge_rekeys_links_without_relationship_writes(self):
        source = self.task("Triage", owner=self.outsider, visibility="restricted")
        canonical = self.task(visibility="restricted")
        self.share(canonical, self.reader)
        with actor_context(self.outsider):
            moved = self.Link.objects.create(target=source, url="https://example.test/source")
            collision = self.Link.objects.create(target=source, url="https://example.test/shared")
        with actor_context(self.owner):
            retained = self.Link.objects.create(target=canonical, url=collision.url)
        self.assertTrue(self.scoped(moved, self.outsider))
        self.assertFalse(self.scoped(moved, self.reader))
        before = list(active_relationship_model().objects.order_by("pk").values())
        self.as_user(source, self.outsider).mark_duplicate(canonical)
        moved.refresh_from_db()
        self.assertEqual(moved.object_id, canonical.pk)
        self.assertFalse(self.Link._base_manager.filter(pk=collision.pk).exists())
        self.assertTrue(self.Link._base_manager.filter(pk=retained.pk).exists())
        self.assertTrue(self.scoped(moved, self.reader))
        self.assertFalse(self.scoped(moved, self.outsider))
        self.assertEqual(list(active_relationship_model().objects.order_by("pk").values()), before)


def test_work_task_access(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_work_task_access.TaskAccessTests", app="angee.intake")


def test_work_task_access_denormalized(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_work_task_access.TaskAccessDenormalizedTests", app="angee.intake")


def test_public_queue_creation(tmp_path: Path):
    """Expected to pass after the public-visibility change in spaces lands."""
    run_composed_tests(tmp_path, "tests.test_work_task_access.PublicQueueTests", app="angee.work")


def test_queue_permissions(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_work_task_access.QueuePermissionsTests", app="angee.work")
