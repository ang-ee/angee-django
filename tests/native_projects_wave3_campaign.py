"""Real GraphQL contracts and project-follow rules on the shared emitted graph."""

from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from graphql import parse, validate
from rebac import actor_context, system_context
from rebac.backends import backend
from rebac.schema.parser import parse_zed

from angee.graphql.schema import GraphQLSchemas
from angee.testing.permissions import install_permission_schema
from tests.native_intake_capture import IntakeAccessCase
from tests.native_work import WorkCase
from tests.test_work_task_access import withhold_assignees


class ProjectSurfaceCampaign(WorkCase):
    graphql = IntakeAccessCase.graphql

    def person(self, name):
        return super().person(f"{self._testMethodName}-{name}")

    def test_promoted_phase_discloses_only_name_to_requester_and_is_not_a_query_axis(self):
        task = self.task()
        self.share(task, self.reader)
        with system_context(reason="tests.t3.phase_requester"):
            need = apps.get_model("intake", "Need").objects.create(
                task=task,
                party=self.Person._base_manager.get(user=self.reader),
                body="Request",
            )
            project = self.Project.objects.create(title="Private project", owner=self.owner, converted_from=task)
            phase = self.Milestone.objects.create(project=project, name="Selected phase")
            project.set_current_milestone(phase)
        need.with_actor(self.owner).decide_access("approve")
        query = "{ project_tasks { id promoted_phase project { id } milestone { id } } projects { id } }"
        before = self.graphql(query, {}, user=self.reader)
        self.assertIsNone(before["project_tasks"][0]["promoted_phase"])
        active = backend()
        original = active.schema()
        schema = deepcopy(original)
        fragment = parse_zed("definition projects/task { permission read_promoted_phase = requester }").definitions[0]
        schema.definitions = [
            definition.extend(permission_arms=fragment.permissions)
            if definition.resource_type == "projects/task"
            else definition
            for definition in schema.definitions
        ]
        install_permission_schema(schema, active=active)
        self.addCleanup(install_permission_schema, original, active=active)
        data = self.graphql(query, {}, user=self.reader)
        self.assertEqual(
            data,
            {
                "project_tasks": [{"id": task.sqid, "promoted_phase": phase.name, "project": None, "milestone": None}],
                "projects": [],
            },
        )
        self.assertFalse(self.Project.objects.as_user(self.reader).filter(pk=project.pk).exists())
        self.assertFalse(self.Milestone.objects.as_user(self.reader).filter(pk=phase.pk).exists())
        with actor_context(self.reader):
            self.assertFalse(task.promoted_projects.exists())
        gql = GraphQLSchemas.from_discovery().build("public")
        for document in (
            "{ project_tasks(where: {promoted_phase: {_is_null: false}}) { id } }",
            "{ project_tasks(order_by: {promoted_phase: asc}) { id } }",
            "{ project_tasks_groups(group_by: [{field: PROMOTED_PHASE}]) { aggregate { count } } }",
        ):
            self.assertTrue(validate(gql._schema, parse(document)), document)
        resource = next(row for row in gql.angee_resources if row.model_label == "projects.Task")
        self.assertNotIn("promoted_phase", resource.query.axes)
        self.assertNotIn("promoted_phase", resource.aggregate_fields)

    def test_task_permissions_and_phase_projections_have_constant_list_cost(self):
        with system_context(reason="tests.t3.permission_page"):
            for index in range(12):
                task = self.task(title=f"Request {index:02d}", assignee=self.assignee)
                project = self.Project.objects.create(title=f"Project {index}", owner=self.owner, converted_from=task)
                milestone = self.Milestone.objects.create(project=project, name=f"Phase {index}")
                project.set_current_milestone(milestone)
        query = """query($limit: Int!) {
          project_tasks(limit: $limit, order_by: {title: asc}) { id permissions promoted_phase }
        }"""
        for user, expected in (
            (self.owner, {"write", "share", "delete", "narrow", "widen", "comment"}),
            (self.assignee, {"write", "narrow", "comment"}),
            (self.member, {"comment"}),
        ):
            counts = []
            for limit in (1, 6, 12):
                self.graphql(query, {"limit": limit}, user=user)
                with (
                    patch(
                        "angee.graphql.capabilities.permission_annotations",
                        side_effect=AssertionError("Per-record permission fallback"),
                    ),
                    CaptureQueriesContext(connection) as queries,
                ):
                    rows = self.graphql(query, {"limit": limit}, user=user)["project_tasks"]
                self.assertEqual(len(rows), limit)
                self.assertTrue(all(set(row["permissions"]) == expected for row in rows), rows)
                self.assertTrue(all((row["promoted_phase"] is not None) == (user == self.owner) for row in rows))
                counts.append(len(queries))
            self.assertEqual(counts, [counts[0]] * 3)

    def test_project_and_milestone_capabilities_batch_and_revoke_with_project_read(self):
        with system_context(reason="tests.t3.project_capabilities"):
            for index in range(6):
                project = self.Project.objects.create(title=f"Project {index}", owner=self.owner)
                self.share(project, self.reader)
                self.Milestone.objects.create(project=project, name=f"Phase {index}")
        query = """query($limit: Int!) {
          projects(limit: $limit) { id permissions }
          project_milestones(limit: $limit) { id permissions }
        }"""
        for user, project_expected, milestone_expected in (
            (self.owner, {"write", "share", "delete"}, {"write", "reach"}),
            (self.reader, set(), set()),
        ):
            counts = []
            for limit in (1, 6):
                self.graphql(query, {"limit": limit}, user=user)
                with (
                    patch(
                        "angee.graphql.capabilities.permission_annotations",
                        side_effect=AssertionError("Per-record permission fallback"),
                    ),
                    CaptureQueriesContext(connection) as queries,
                ):
                    result = self.graphql(query, {"limit": limit}, user=user)
                self.assertEqual(len(result["projects"]), limit)
                self.assertEqual(len(result["project_milestones"]), limit)
                self.assertTrue(all(set(row["permissions"]) == project_expected for row in result["projects"]))
                self.assertTrue(
                    all(set(row["permissions"]) == milestone_expected for row in result["project_milestones"])
                )
                counts.append(len(queries))
            self.assertEqual(counts[0], counts[1])
        with actor_context(self.owner):
            for project in self.Project.objects.all():
                project.revoke_record_access("reader", self.reader)
        self.assertEqual(
            self.graphql(query, {"limit": 6}, user=self.reader),
            {
                "projects": [],
                "project_milestones": [],
            },
        )

    def test_stage_position_is_a_public_task_sort_axis(self):
        self.task(title="Later", stage="Doing")
        self.task(title="Earlier", stage="Ready")
        result = self.graphql(
            "{ project_tasks(order_by: {stage__position: asc}) { title } }",
            {},
            user=self.member,
        )
        self.assertEqual(result["project_tasks"], [{"title": "Earlier"}, {"title": "Later"}])

    def test_visibility_hook_refusal_is_returned_in_band_without_revision_change(self):
        task = self.task()
        before = task.revision
        with patch.object(self.Task, "validate_visibility", side_effect=ValidationError({"visibility": "Blocked"})):
            result = self.graphql(
                """mutation($task: ID!) {
              set_task_visibility(id: $task, visibility: RESTRICTED) { ok validation_errors }
            }""",
                {"task": task.sqid},
                user=self.owner,
            )["set_task_visibility"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["validation_errors"], {"visibility": ["Blocked"]})
        task.refresh_from_db()
        self.assertEqual((task.visibility, task.revision), ("inherited", before))

    def test_project_phase_and_status_rules_preserve_dates_and_move_backwards(self):
        task = self.task(visibility="restricted")
        with actor_context(self.owner):
            project = self.as_user(task).promote_to_project()
        with system_context(reason="tests.t3.phase_rules"):
            opening = self.Milestone.objects.create(project=project, name="Opening", active_stage=self.stages["Active"])
            delivery = self.Milestone.objects.create(
                project=project, name="Delivery", active_stage=self.stages["Final"]
            )
        with actor_context(self.owner):
            project = self.as_user(project)
            project.target_date = date(2027, 1, 1)
            project.save(update_fields=("target_date",))
            project.set_current_milestone(delivery)
            self.as_user(delivery).mark_reached()
            project.complete()
            project.set_current_milestone(opening)
            task.refresh_from_db()
            self.assertEqual(task.stage_id, self.stages["Active"].pk)
            project.drop()
            task.refresh_from_db()
            self.assertEqual(task.stage_id, self.stages["Canceled"].pk)
            project.resume()
            task.refresh_from_db()
            self.assertEqual(task.stage_id, self.stages["Active"].pk)
            before = (task.revision, task.updated_at)
            project.sync_source_task_stage()
            task.refresh_from_db()
            self.assertEqual((task.revision, task.updated_at), before)
        delivery.refresh_from_db()
        self.assertIsNotNone(delivery.reached_at)
        self.assertEqual(project.current_milestone_id, opening.pk)
        self.assertEqual(project.target_date, date(2027, 1, 1))
        self.assertEqual(task.visibility, "restricted")

    def test_mapped_stage_configuration_cannot_be_cleared_concealed_or_moved(self):
        task = self.task()
        with actor_context(self.owner):
            project = self.as_user(task).promote_to_project()
        with system_context(reason="tests.t3.mapping"):
            self.Milestone.objects.create(project=project, name="Mapped", active_stage=self.stages["Final"])
            other = self.Queue.objects.create(name="Other", key="OTHER", owner=self.manager)
            for field, value in (("rule_owned", False), ("conceals", True), ("queue", other)):
                for method in ("clean", "save"):
                    with self.subTest(field=field, method=method):
                        stage = self.Stage._base_manager.get(pk=self.stages["Final"].pk)
                        setattr(stage, field, value)
                        with self.assertRaises(ValidationError):
                            getattr(stage, method)()
            persisted = self.Stage._base_manager.get(pk=self.stages["Final"].pk)
            self.assertEqual(
                (persisted.rule_owned, persisted.conceals, persisted.queue_id), (True, False, self.queue.pk)
            )

    def test_public_queue_withheld_scope_removes_stages_tasks_and_keeps_managers(self):
        with system_context(reason="tests.t3.public_queue"):
            self.queue.visibility = "public"
            self.queue.save(update_fields=("visibility",))
        self.task(assignee=self.outsider)
        self.task(assignee=self.moderator)
        task = self.task()
        self.assertTrue(self.scoped(task, self.outsider))
        withhold_assignees(self)
        for row in (self.queue, self.stages["Ready"], task):
            self.assertFalse(self.scoped(row, self.outsider))
            self.assertTrue(self.scoped(row, self.moderator))


class ProjectSurfaceDenormalizedCampaign(ProjectSurfaceCampaign):
    storage = "denormalized"
