"""Task board intent respects persisted project ranks and native write admission."""

import math
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections, models, transaction
from django.test import RequestFactory
from rebac import actor_context, system_context
from rebac.backends import backend
from rebac.schema.parser import parse_zed
from rebac.testing import install_schema

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase

_MOVE = """mutation Move($id: String!, $set: project_tasks_set_input!) {
  update_project_tasks_by_pk(pk_columns: {id: $id}, _set: $set) { id sort_order }
}"""


class TaskProjectOrderingTests(WorkCase):
    def setUp(self):
        super().setUp()
        with system_context(reason="tests.task_order.project"):
            self.project = self.Project.objects.create(title="Project", owner=self.owner)

    def ordered_task(self, title, rank, **values):
        return self.task(None, title=title, sort_order=rank, queue=None, project=self.project, **values)

    def execute_move(self, row, preceding=(), following=(), *, user=None, **patch):
        from angee.graphql.schema import GraphQLSchemas

        user = user or self.owner
        request = RequestFactory().post("/graphql/")
        request.user = user
        with actor_context(user):
            return GraphQLSchemas.from_discovery().build("public").execute_sync(
                _MOVE,
                variable_values={
                    "id": row.sqid,
                    "set": {"task_order": {"preceding": list(preceding), "following": list(following)}, **patch},
                },
                context_value=SimpleNamespace(request=request),
            )

    def test_hidden_other_assignee_rank_is_not_a_lane_midpoint_collision(self):
        before = self.ordered_task("Before", 0, assignee=self.owner)
        hidden = self.ordered_task(
            "Other assignee", 1024, owner=self.outsider, assignee=self.outsider, visibility="restricted",
        )
        after = self.ordered_task("After", 2048, assignee=self.owner)
        moving = self.ordered_task("Moving", 4096, assignee=self.owner)
        self.assertFalse(self.scoped(hidden, self.owner))
        result = self.execute_move(moving, [before.sqid], [after.sqid])
        self.assertIsNone(result.errors, result.errors)
        moving.refresh_from_db()
        hidden.refresh_from_db()
        self.assertEqual(moving.sort_order, 1536)
        self.assertEqual(hidden.sort_order, 1024)
        self.assertEqual(result.data["update_project_tasks_by_pk"]["sort_order"], 1536)
        self.assertEqual(list(self.Task._base_manager.filter(project=self.project).order_by(
            "sort_order",
        ).values_list("pk", flat=True)), [before.pk, hidden.pk, moving.pk, after.pk])

    def test_board_drop_repairs_exhausted_context_then_preserves_intent(self):
        before = self.ordered_task("Before", 1.0)
        after = self.ordered_task("After", math.nextafter(1.0, math.inf))
        moving = self.ordered_task("Moving", 4096)
        result = self.execute_move(moving, [before.sqid], [after.sqid])
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(list(self.Task._base_manager.filter(project=self.project).order_by(
            "sort_order",
        ).values_list("pk", flat=True)), [before.pk, moving.pk, after.pk])

    def test_mixed_project_visible_neighbors_do_not_define_task_scope(self):
        before = self.ordered_task("Before", 0)
        after = self.ordered_task("After", 2048)
        moving = self.ordered_task("Moving", 4096)
        foreign = self.task(None, queue=None, title="Other project scope", sort_order=100000)
        result = self.execute_move(moving, [before.sqid, foreign.sqid], [foreign.sqid, after.sqid])
        self.assertIsNotNone(result.errors, "A repeated neighbor must be refused before resolving scope.")
        result = self.execute_move(moving, [before.sqid, foreign.sqid], [after.sqid])
        self.assertIsNone(result.errors, result.errors)
        moving.refresh_from_db()
        self.assertEqual(moving.sort_order, 1024)

    def test_projectless_scope_and_full_project_append_are_not_lane_scoped(self):
        first = self.task(None, queue=None, title="Personal", sort_order=0)
        tail = self.task(None, queue=None, title="Other lane", sort_order=8192, assignee=self.outsider)
        foreign = self.ordered_task("Project neighbor", 1e9)
        result = self.execute_move(first, [foreign.sqid], [])
        self.assertIsNone(result.errors, result.errors)
        first.refresh_from_db()
        tail.refresh_from_db()
        self.assertEqual(first.sort_order, tail.sort_order + 1024)
        self.assertIsNone(first.project_id)

    def test_unreadable_or_stale_neighbors_roll_back_the_lane_patch(self):
        first = self.ordered_task("First", 0)
        last = self.ordered_task("Last", 2048)
        moving = self.ordered_task("Moving", 4096, assignee=self.owner)
        hidden = self.ordered_task("Hidden", 1024, owner=self.outsider, visibility="restricted")
        for preceding, following in (([hidden.sqid], [last.sqid]), ([last.sqid], [first.sqid])):
            result = self.execute_move(moving, preceding, following, assignee=None)
            self.assertIsNotNone(result.errors)
            moving.refresh_from_db()
            self.assertEqual((moving.sort_order, moving.assignee_id), (4096, self.owner.pk))

    def test_read_only_actor_cannot_reorder_an_extension_only_write(self):
        before = self.ordered_task("Before", 0)
        moving = self.ordered_task("Moving", 4096)
        self.share(before, self.reader)
        self.share(moving, self.reader)
        result = self.execute_move(moving, [], [before.sqid], user=self.reader)
        self.assertIsNotNone(result.errors)
        moving.refresh_from_db()
        self.assertEqual(moving.sort_order, 4096)

    def test_literal_rank_writes_and_creation_remain_exact(self):
        first = self.ordered_task("First", 0)
        moving = self.ordered_task("Moving", 4096)
        data = self.graphql(_MOVE, {"id": moving.sqid, "set": {"sort_order": 12.5}})
        self.assertEqual(data["update_project_tasks_by_pk"]["sort_order"], 12.5)
        moving.refresh_from_db()
        self.assertEqual(moving.sort_order, 12.5)
        with actor_context(self.owner):
            appended = self.Task.objects.create(title="Native append", project=self.project, owner=self.owner)
        self.assertEqual(appended.sort_order, moving.sort_order + 1024)
        first.refresh_from_db()
        self.assertEqual(first.sort_order, 0)

    def test_rank_field_permission_is_enforced_for_neighbor_intent(self):
        before = self.ordered_task("Before", 0)
        moving = self.ordered_task("Moving", 4096)
        active = backend()
        original = active.schema()
        schema = deepcopy(original)
        fragment = parse_zed("definition projects/task { permission write__sort_order = nil }").definitions[0]
        schema.definitions = [
            replace(definition, permissions=(*definition.permissions, *fragment.permissions))
            if definition.resource_type == "projects/task" else definition
            for definition in schema.definitions
        ]
        install_schema(schema, backend=active)
        try:
            result = self.execute_move(moving, [], [before.sqid])
            self.assertIsNotNone(result.errors)
            moving.refresh_from_db()
            self.assertEqual(moving.sort_order, 4096)
        finally:
            install_schema(original, backend=active)

    def test_literal_rank_and_ordering_intent_cannot_silently_override_each_other(self):
        first = self.ordered_task("Before", 0)
        moving = self.ordered_task("Moving", 4096)
        result = self.execute_move(moving, [], [first.sqid], sort_order=12.5)
        self.assertIsNotNone(result.errors)
        moving.refresh_from_db()
        self.assertEqual(moving.sort_order, 4096)

    def test_native_multirow_creation_allocates_each_omitted_rank_once(self):
        with system_context(reason="tests.task_order.bulk"):
            rows = [
                self.Task(title="One", project=self.project, owner=self.owner),
                self.Task(title="Two", project=self.project, owner=self.owner),
                self.Task(title="Explicit", project=self.project, owner=self.owner, sort_order=8192),
            ]
            self.Task._base_manager.bulk_create(rows)
            self.assertEqual([row.sort_order for row in rows], [9216, 10240, 8192])
            self.assertEqual({row.sub_sort_order for row in rows}, {1024})
            forest = [self.Task(title=f"Forest {index}", project=self.project, owner=self.owner) for index in range(2)]
            self.Task.objects.create_forest(forest)
        self.assertEqual([row.sort_order for row in forest], [11264, 12288])
        self.assertEqual({row.sub_sort_order for row in [*rows, *forest]}, {1024})
        self.assertTrue(all(row.path for row in forest))

    def test_forest_stages_null_parent_ranks_then_captures_final_parent_context_once(self):
        first = self.ordered_task("First parent", 0)
        second = self.ordered_task("Second parent", 1024)
        self.ordered_task("Existing child", 2048, parent=first, sub_sort_order=1024)
        with system_context(reason="tests.task_order.forest"):
            common = {"project": self.project, "owner": self.owner}
            parent_a = self.Task(title="Unsaved A", **common)
            parent_b = self.Task(title="Unsaved B", **common)
            child_a = self.Task(title="Saved parent append", parent=first, **common)
            child_b = self.Task(title="Saved parent explicit", parent=second, sub_sort_order=1024, **common)
            child_c = self.Task(title="Unsaved parent explicit", parent=parent_a, sub_sort_order=1024, **common)
            child_d = self.Task(title="Other unsaved parent", parent=parent_b, sub_sort_order=1024, **common)
            child_e = self.Task(title="Same unsaved parent append", parent=parent_a, **common)
            rows = [child_a, child_b, parent_a, child_c, parent_b, child_d, child_e]
            self.Task.objects.create_forest(rows)
        self.assertEqual(child_a.sub_sort_order, 2048)
        self.assertEqual([child_b.sub_sort_order, child_c.sub_sort_order, child_d.sub_sort_order], [1024] * 3)
        self.assertEqual(child_e.sub_sort_order, 2048)
        self.assertEqual(child_c.parent_id, parent_a.pk)
        self.assertEqual(child_d.parent_id, parent_b.pk)
        for row in rows:
            stored = self.Task._base_manager.get(pk=row.pk)
            self.assertEqual((stored.parent_id, stored.sub_sort_order), (row.parent_id, row.sub_sort_order))
            versions = list(self.Task.history.filter(id=row.pk).values_list(
                "history_type", "parent_id", "sub_sort_order",
            ))
            self.assertEqual(versions, [("+", row.parent_id, row.sub_sort_order)])

    def test_native_queryset_and_upsert_moves_lock_their_source_and_destination(self):
        with system_context(reason="tests.task_order.native_moves"):
            other = self.Project.objects.create(title="Destination", owner=self.owner)
            row = self.ordered_task("Moving", 0)
            self.Task._base_manager.filter(pk=row.pk).update(project_id=models.Value(other.pk))
            row.refresh_from_db()
            self.assertEqual(row.project_id, other.pk)
            row.project = self.project
            self.Task._base_manager.bulk_create(
                [row], update_conflicts=True, update_fields=("project",), unique_fields=("pk",),
            )
            row.refresh_from_db()
            self.assertEqual(row.project_id, self.project.pk)

    @skipUnless(connection.vendor == "postgresql", "Requires native PostgreSQL transactional ordering locks.")
    def test_independent_projects_do_not_wait_for_an_ingest_transaction(self):
        with system_context(reason="tests.task_order.independent_setup"):
            other = self.Project.objects.create(title="Independent project", owner=self.owner)
            row = self.Task.objects.create(title="Independent task", project=other, owner=self.owner)
        for operation in ("insert", "save", "queryset", "bulk_create", "create_forest", "bulk_update", "graphql"):
            with self.subTest(operation=operation):
                acquired, release = Event(), Event()
                def ingest():
                    try:
                        with system_context(reason="tests.task_order.ingest"), transaction.atomic():
                            self.Task.objects.create(title="Ingested", project_id=self.project.pk, owner=self.owner)
                            acquired.set()
                            self.assertTrue(release.wait(10))
                    finally:
                        connections.close_all()
                def independent():
                    try:
                        with system_context(reason="tests.task_order.independent"):
                            selected = self.Task.system_queryset().filter(pk=row.pk)
                            target = selected.get()
                            if operation == "insert":
                                self.Task.objects.create(title="Append independent", project=other, owner=self.owner)
                            elif operation == "save":
                                target.sort_order = -1024
                                target.save(update_fields=("sort_order",))
                            elif operation == "queryset":
                                selected.update(sort_order=-2048)
                            elif operation == "bulk_create":
                                self.Task._base_manager.bulk_create([
                                    self.Task(title="Bulk independent", project=other, owner=self.owner),
                                ])
                            elif operation == "create_forest":
                                self.Task.objects.create_forest([
                                    self.Task(title="Forest independent", project=other, owner=self.owner),
                                ])
                            elif operation == "bulk_update":
                                target.sort_order = -3072
                                self.Task.objects.bulk_update([target], ("sort_order",))
                            else:
                                result = self.execute_move(target)
                                self.assertIsNone(result.errors, result.errors)
                    finally:
                        connections.close_all()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    held = pool.submit(ingest)
                    try:
                        self.assertTrue(acquired.wait(5))
                        pool.submit(independent).result(timeout=5)
                    finally:
                        release.set()
                    held.result(timeout=10)

    @skipUnless(connection.vendor == "postgresql", "Requires native PostgreSQL transactional ordering locks.")
    def test_concurrent_project_move_refuses_a_stale_ordering_context(self):
        row = self.ordered_task("Moves while waiting", 0)
        with system_context(reason="tests.task_order.stale_setup"):
            other = self.Project.objects.create(title="Destination", owner=self.owner)
        waiting = Event()
        def reorder():
            try:
                def observe(execute, sql, params, many, context):
                    if "pg_advisory_xact_lock" in sql:
                        waiting.set()
                    return execute(sql, params, many, context)
                with connection.execute_wrapper(observe), system_context(reason="tests.task_order.stale_write"):
                    try:
                        self.Task._base_manager.filter(pk=row.pk).update(sort_order=4096)
                    except ValidationError:
                        return "stale"
                return "changed"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with system_context(reason="tests.task_order.move"), self.Task.ordering_write(
                using=connection.alias, project_ids=(self.project.pk,),
            ):
                future = pool.submit(reorder)
                self.assertTrue(waiting.wait(5))
                self.Task._base_manager.filter(pk=row.pk).update(project_id=other.pk)
            self.assertEqual(future.result(timeout=10), "stale")
        row.refresh_from_db()
        self.assertEqual((row.project_id, row.sort_order), (other.pk, 0))

    @skipUnless(connection.vendor == "postgresql", "Requires native PostgreSQL transactional ordering locks.")
    def test_cross_project_children_still_share_their_parent_order(self):
        parent = self.ordered_task("Shared parent", 0)
        with system_context(reason="tests.task_order.cross_project_parent"):
            other = self.Project.objects.create(title="Other project", owner=self.owner)
        ready = Barrier(2, timeout=5)
        def append(project_id):
            try:
                ready.wait()
                with system_context(reason="tests.task_order.cross_project_append"):
                    return self.Task.objects.create(
                        title="Child", project_id=project_id, parent_id=parent.pk, owner=self.owner,
                    ).pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            keys = list(pool.map(append, (self.project.pk, other.pk)))
        self.assertEqual(list(self.Task._base_manager.filter(pk__in=keys).order_by(
            "sub_sort_order",
        ).values_list("sub_sort_order", flat=True)), [1024, 2048])

    @skipUnless(connection.vendor == "postgresql", "Requires native PostgreSQL transactional ordering locks.")
    def test_concurrent_native_appends_share_the_ordering_transaction(self):
        self.ordered_task("Before", 0)
        self.ordered_task("Tail", 2048)
        task_model = self.Task
        project_id, owner_id = self.project.pk, self.owner.pk
        ready = Barrier(2, timeout=5)

        def write(index):
            try:
                ready.wait()
                with system_context(reason="tests.task_order.concurrent"), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    return task_model.objects.create(
                        title=f"Concurrent append {index}", project_id=project_id, owner_id=owner_id,
                    ).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(write, (0, 1)))
        ranks = list(task_model._base_manager.filter(project_id=project_id).order_by(
            "sort_order",
        ).values_list("sort_order", flat=True))
        self.assertEqual(ranks, [0, 2048, 3072, 4096])


def test_task_project_ordering(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_task_project_ordering.TaskProjectOrderingTests", app="angee.work")
