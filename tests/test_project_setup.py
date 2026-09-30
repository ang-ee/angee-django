"""Transactional project setup and actor-scoped task projections on emitted models."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context

from angee.base.mixins import CreationKeyConflict
from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


class ProjectSetupFixture(WorkCase):
    """Shared native actors and setup choices for the project contract groups."""

    def setUp(self):
        super().setUp()
        self.Need = apps.get_model("intake", "Need")
        self.Round = apps.get_model("proposals", "Round")
        self.Vault = apps.get_model("knowledge", "Vault")
        self.requester = self.Person._base_manager.get(user=self.reader)
        self.share(self.requester, self.manager)
        self.share(self.requester.party_ptr, self.manager)
        self.share(self.member, self.manager, "directory_reader")
        self.share(self.manager, self.manager, "directory_reader")
        self.source = self.task(owner=self.manager)
        with system_context(reason="tests.project_setup.fixture"):
            storage_backend = apps.get_model("storage", "Backend").objects.create(
                slug="local", backend_class="local",
            )
            apps.get_model("storage", "Drive").objects.create(
                slug="assets", name="Assets", prefix="assets", backend=storage_backend,
            )
            self.template = self.Vault.objects.create(name="Reference", owner=self.manager)
            self.need = self.Need.objects.create(task=self.source, body="Request details")
        deadline = timezone.now() + timedelta(days=3)
        self.configuration = {
            "team": str(self.queue.group_ptr.sqid),
            "party": str(self.Person._base_manager.get(user=self.reader).sqid),
            "milestones": [
                {"name": "Discovery", "active_stage": str(self.stages["Active"].sqid),
                 "start_date": timezone.localdate().isoformat(), "target_date": deadline.date().isoformat()},
                {"name": "Delivery", "active_stage": str(self.stages["Final"].sqid)},
            ],
            "vault_template": str(self.template.sqid),
            "round": {
                "template": {"name": "Review", "last_call_at": (deadline - timedelta(days=1)).isoformat(),
                             "submission_deadline": deadline.isoformat(), "tracks": True,
                             "opens_after": "Discovery", "topics": [{"key": "approach", "name": "Approach"}]},
                "facilitator": str(self.manager.sqid), "responders": [str(self.member.sqid)],
                "team": str(self.queue.group_ptr.sqid),
                "requester_party": str(self.Person._base_manager.get(user=self.reader).sqid),
                "clarification_queue": str(self.queue.sqid),
            },
        }

    def native_configuration(self, configuration=None):
        """Non-GraphQL callers resolve references with the actor-scoped query owner."""
        values = deepcopy(configuration or self.configuration)
        def row(model, value):
            return apps.get_model(model).objects.with_actor(self.manager).with_action("read").from_public_id(value)
        for key, model in (("team", "spaces.Group"), ("party", "parties.Party"), ("vault_template", "knowledge.Vault")):
            values[key] = row(model, values[key])
        for milestone in values["milestones"]:
            milestone["active_stage"] = row("work.Stage", milestone["active_stage"])
        round = values["round"]
        for key, model in (("facilitator", "iam.User"), ("team", "spaces.Group"),
                           ("requester_party", "parties.Party"), ("clarification_queue", "work.Queue")):
            if key in round:
                round[key] = row(model, round[key])
        round["responders"] = [row("iam.User", value) for value in round["responders"]]
        return values

    def setup_project(self, configuration=None, key="setup", expected_revision=None):
        with actor_context(self.manager):
            return self.Project.objects.setup_from_task(
                self.as_user(self.source, self.manager), configuration=self.native_configuration(configuration),
                client_creation_key=key, expected_revision=expected_revision,
            )


class ProjectSetupCase(ProjectSetupFixture):
    """Exercise transactional setup and filing on production contributors."""

    def test_lifecycle_date_changes_only_with_status(self):
        project = self.setup_project()
        self.assertIsNone(project.status_changed_at)
        self.assertTrue(project.on_path)
        with actor_context(self.manager):
            project.pause()
            self.assertFalse(project.on_path)
            paused_at = project.status_changed_at
            project.resume()
            self.assertTrue(project.on_path)
            self.assertGreater(project.status_changed_at, paused_at)
            project.complete()
            completed_at = project.status_changed_at
            self.assertFalse(project.on_path)
            project.complete()
            self.assertEqual(project.status_changed_at, completed_at)
            project.title = "Renamed project"
            project.save(update_fields=("title", "updated_at"))
            self.assertEqual(project.status_changed_at, completed_at)
            project.drop()
        self.assertIsNotNone(completed_at)
        self.assertGreater(project.status_changed_at, completed_at)

    def test_setup_is_atomic_and_replay_does_not_reset_later_edits(self):
        initial_revision = self.source.revision
        project = self.setup_project(expected_revision=initial_revision)
        self.assertEqual(project.team_id, self.queue.pk)
        self.assertEqual(self.Milestone._base_manager.filter(project=project).count(), 2)
        self.assertEqual(apps.get_model("projects", "ProjectBinding")._base_manager.filter(project=project).count(), 1)
        self.assertEqual(self.Round._base_manager.filter(project=project).count(), 1)
        self.assertEqual(self.Need._base_manager.get(pk=self.need.pk).party_id,
                         self.Person._base_manager.get(user=self.reader).pk)
        with actor_context(self.manager):
            project.title = "Renamed after setup"
            project.save(update_fields=("title",))
        replay = self.setup_project(expected_revision=initial_revision)
        self.assertEqual(replay.pk, project.pk)
        self.assertEqual(replay.title, "Renamed after setup")
        changed = deepcopy(self.configuration)
        changed["round"]["responders"] = []
        with self.assertRaises(CreationKeyConflict):
            self.setup_project(changed)

    def test_setup_receipt_scope_and_ordinary_project_insert_semantics(self):
        first = self.setup_project()
        receipts = apps.get_model("projects", "ProjectSetupReceipt")
        self.assertEqual(receipts.objects.get(task=self.source).actor_id, self.manager.pk)
        second_source = self.task(owner=self.manager, title="Second request")
        with actor_context(self.manager):
            second = self.Project.objects.setup_from_task(
                self.as_user(second_source, self.manager), configuration=self.native_configuration(),
                client_creation_key="setup",
            )
        self.assertNotEqual(first.pk, second.pk)
        # The full schema exists only after this composed host has booted.
        from angee.graphql.schema import GraphQLSchemas
        schema = GraphQLSchemas.from_discovery().build("console")
        self.assertNotIn("client_creation_key", schema._schema.mutation_type.fields["insert_projects_one"].args)

    def test_setup_failure_rolls_back_all_new_acts_and_resumes_partial_project(self):
        invalid = deepcopy(self.configuration)
        invalid["round"]["template"]["opens_after"] = "Missing phase"
        with self.assertRaises(ValidationError):
            self.setup_project(invalid)
        self.assertFalse(self.Project._base_manager.filter(converted_from=self.source).exists())
        self.assertIsNone(self.Need._base_manager.get(pk=self.need.pk).party_id)
        self.assertEqual(self.Vault._base_manager.count(), 1)
        with actor_context(self.manager):
            partial = self.source.with_actor(self.manager).promote_to_project()
            milestone = self.Milestone.objects.create(project=partial, name="Discovery")
        project = self.setup_project()
        self.assertEqual(project.pk, partial.pk)
        self.assertEqual(project.current_milestone_id, milestone.pk)
        self.assertEqual(self.Milestone._base_manager.filter(project=project).count(), 2)
        milestone.refresh_from_db()
        self.assertEqual(milestone.active_stage_id, self.stages["Active"].pk)
        self.assertEqual(milestone.target_date.isoformat(), self.configuration["milestones"][0]["target_date"])

    def test_invalid_setup_shapes_roll_back_and_return_action_errors(self):
        for field, value in (
            ("milestones", [{"name": ""}]),
            ("round", {**self.configuration["round"], "template": {
                **self.configuration["round"]["template"], "opens_after": "missing",
            }}),
        ):
            invalid = {**self.configuration, field: value}
            data = self.graphql(
                """mutation($id: ID!, $configuration: ProjectSetupInput!) {
                  setup_project(id: $id, configuration: $configuration, client_creation_key: "invalid") {
                    ok code
                  }
                }""", {"id": self.source.sqid, "configuration": invalid}, user=self.manager,
            )["setup_project"]
            self.assertFalse(data["ok"])
            self.assertFalse(self.Project._base_manager.filter(converted_from=self.source).exists())

    def test_resume_round_fills_missing_requester_and_routing(self):
        partial = deepcopy(self.configuration)
        partial["round"].pop("requester_party")
        partial["round"].pop("clarification_queue")
        with actor_context(self.manager):
            project = self.source.with_actor(self.manager).promote_to_project()
            project.apply_setup(**self.native_configuration(partial))
        previous_round = self.Round._base_manager.get(project=project)
        completed = self.setup_project()
        previous_round.refresh_from_db()
        self.assertEqual(completed.pk, project.pk)
        self.assertEqual(previous_round.clarification_queue_id, self.queue.pk)
        self.assertEqual(previous_round.requester_party_id, self.requester.pk)
        self.assertEqual(self.Round._base_manager.filter(project=project).count(), 1)

    def test_overdue_counts_follow_current_phase_and_project_lifecycle(self):
        project = self.setup_project()
        with system_context(reason="tests.setup.overdue"):
            self.Milestone.objects.filter(pk=project.current_milestone_id).update(
                start_date=None, target_date=timezone.localdate() - timedelta(days=1),
            )
        query = """query($project: String!, $task: String!) {
          projects_by_pk(id: $project) { overdue_milestone_count }
          project_tasks_by_pk(id: $task) { overdue_milestone_count }
        }"""
        variables = {"project": project.sqid, "task": self.source.sqid}
        result = self.graphql(query, variables, user=self.manager)
        self.assertEqual(result["projects_by_pk"]["overdue_milestone_count"], 1)
        self.assertEqual(result["project_tasks_by_pk"]["overdue_milestone_count"], 1)
        with actor_context(self.manager):
            project.complete()
        result = self.graphql(query, variables, user=self.manager)
        self.assertEqual(result["projects_by_pk"]["overdue_milestone_count"], 0)
        self.assertEqual(result["project_tasks_by_pk"]["overdue_milestone_count"], 0)

    def test_named_overdue_phase_does_not_count_another_phase(self):
        project = self.setup_project()
        with system_context(reason="tests.setup.named_overdue"):
            self.Milestone.objects.filter(project=project, name="Delivery").update(
                target_date=timezone.localdate() - timedelta(days=1),
            )
        query = """query($id: String!, $phase: String!) {
          projects_by_pk(id: $id) { overdue_milestone_count(milestone_name: $phase) }
        }"""
        for phase, count in (("Discovery", 0), ("Delivery", 1)):
            data = self.graphql(query, {"id": project.sqid, "phase": phase}, user=self.manager)
            self.assertEqual(data["projects_by_pk"]["overdue_milestone_count"], count)

    def test_graphql_setup_and_actor_safe_state(self):
        data = self.graphql(
            """mutation Setup($id: ID!, $configuration: ProjectSetupInput!, $key: String!) {
              setup_project(id: $id, configuration: $configuration, client_creation_key: $key) {
                ok id message validation_errors
              }
            }""", {"id": self.source.sqid, "configuration": self.configuration, "key": "graphql"},
            user=self.manager,
        )["setup_project"]
        self.assertTrue(data["ok"], data)
        query = """query($id: String!) {
          project_tasks_by_pk(id: $id) { setup_state overdue_milestone_count }
          projects { setup_state overdue_milestone_count }
        }"""
        rows = self.graphql(query, {"id": self.source.sqid}, user=self.manager)
        self.assertEqual(rows["project_tasks_by_pk"]["setup_state"], "COMPLETE")
        self.share(self.source, self.reader)
        rows = self.graphql(query, {"id": self.source.sqid}, user=self.reader)
        self.assertEqual(rows["project_tasks_by_pk"]["setup_state"], "NOT_SET_UP")
        self.assertEqual(rows["project_tasks_by_pk"]["overdue_milestone_count"], 0)
        self.assertEqual(rows["projects"], [])

    def test_setup_replay_requires_current_write_permissions(self):
        self.setup_project()
        with actor_context(self.outsider), self.assertRaises(PermissionDenied):
            self.Project.objects.setup_from_task(
                self.source.with_actor(self.outsider), configuration=self.configuration, client_creation_key="setup",
            )

    def test_file_task_with_need_replay_conflict_and_rollback(self):
        values = dict(queue=self.as_user(self.queue, self.manager), title="Direct request", body="Details",
                      party=self.requester.party_ptr.with_actor(self.manager),
                      client_creation_key="filing")
        with actor_context(self.manager):
            task = self.Need.objects.file_task(**values)
            self.assertEqual(self.Need.objects.file_task(**values).pk, task.pk)
            self.assertEqual(self.Need.objects.filter(task=task).count(), 1)
            with self.assertRaises(CreationKeyConflict):
                self.Need.objects.file_task(**{**values, "body": "Changed"})
            with self.assertRaises(ValidationError):
                self.Need.objects.file_task(**{**values, "client_creation_key": "empty", "body": ""})
        self.assertFalse(self.Task._base_manager.filter(client_creation_key="empty").exists())

    def test_graphql_filing_checks_permission_and_replays_the_task(self):
        mutation = """mutation($queue: ID!, $party: ID!) {
          file_task_with_need(queue: $queue, party: $party, title: "Request", body: "Details",
                              client_creation_key: "graphql-filing") { ok id }
        }"""
        variables = {"queue": self.queue.sqid, "party": self.requester.sqid}
        result = self.graphql(mutation, variables, user=self.manager)["file_task_with_need"]
        self.assertTrue(result["ok"])
        self.assertEqual(self.graphql(mutation, variables, user=self.manager)["file_task_with_need"], result)
        self.assertFalse(self.graphql(mutation, variables, user=self.outsider)["file_task_with_need"]["ok"])
        self.share(self.requester.party_ptr, self.member)
        self.assertFalse(self.as_user(self.queue, self.member).has_access("write"))
        self.assertTrue(self.graphql(mutation, variables, user=self.member)["file_task_with_need"]["ok"])

    def test_container_key_filters_only_readable_tasks_without_queue_read(self):
        self.share(self.source, self.reader)
        hidden = self.task(title="Not shared", owner=self.manager)
        self.assertFalse(self.as_user(self.queue, self.reader).has_access("read"))
        data = self.graphql(
            """query Scope($key: String!, $queue: String!) {
              scoped: project_tasks(where: {queue__slug: {_eq: $key}}) { id queue { id } }
              count: project_tasks_aggregate(where: {queue__slug: {_eq: $key}}) { aggregate { count } }
              guarded: project_tasks(where: {queue: {_eq: $queue}}) { id }
            }""", {"key": self.queue.slug, "queue": self.queue.sqid}, user=self.reader,
        )
        self.assertEqual(data["scoped"], [{"id": self.source.sqid, "queue": None}])
        self.assertEqual(data["count"]["aggregate"]["count"], 1)
        self.assertEqual(data["guarded"], [])
        self.assertNotEqual(hidden.pk, self.source.pk)


class ProjectAttentionCase(ProjectSetupFixture):
    """Keep attention correctness and query cost independent of page size."""

    def test_attention_counts_follow_answers_and_exclude_unreadable_questions(self):
        project = self.setup_project()
        round = self.Round._base_manager.get(project=project)
        with actor_context(self.member):
            question = self.as_user(round, self.member).ask("Clarify the request", "Details")
        query = """query Attention($project: String!, $task: String!) {
          projects_by_pk(id: $project) { questions_waiting_for_me questions_passed_on }
          project_tasks_by_pk(id: $task) { questions_waiting_for_me questions_passed_on }
        }"""
        variables = {"project": project.sqid, "task": self.source.sqid}
        waiting = self.graphql(query, variables, user=self.manager)
        self.assertEqual(waiting["projects_by_pk"], {"questions_waiting_for_me": 1, "questions_passed_on": 0})
        self.assertEqual(waiting["project_tasks_by_pk"], waiting["projects_by_pk"])
        with actor_context(self.manager):
            self.as_user(round, self.manager).pass_clarification(question, self.member)
        passed = self.graphql(query, variables, user=self.manager)
        self.assertEqual(passed["projects_by_pk"], {"questions_waiting_for_me": 0, "questions_passed_on": 1})
        with actor_context(self.member):
            self.as_user(question, self.member).message_post("Here is the answer")
        answered = self.graphql(query, variables, user=self.manager)
        self.assertEqual(answered["projects_by_pk"], {"questions_waiting_for_me": 0, "questions_passed_on": 0})
        # Project read alone does not reveal questions surrendered to round managers.
        self.share(project, self.outsider)
        with actor_context(self.member):
            self.as_user(round, self.member).ask("Private question", "Details", audience="managers")
        hidden = self.graphql(query, variables, user=self.outsider)
        self.assertEqual(hidden["projects_by_pk"], {"questions_waiting_for_me": 0, "questions_passed_on": 0})

    def test_attention_projection_cost_does_not_grow_per_row(self):
        project = self.setup_project()
        round = self.Round._base_manager.get(project=project)
        with actor_context(self.member):
            for index in range(5):
                self.as_user(round, self.member).ask(f"Question {index}", "Details")
        query = """query($limit: Int!) { project_tasks(limit: $limit) {
          id setup_state questions_waiting_for_me questions_passed_on overdue_milestone_count
        } }"""
        counts = []
        for limit in (1, 5):
            self.graphql(query, {"limit": limit}, user=self.manager)
            with CaptureQueriesContext(connection) as queries:
                rows = self.graphql(query, {"limit": limit}, user=self.manager)["project_tasks"]
            self.assertEqual(len(rows), limit)
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])


class DashboardVisibilityCase(WorkCase):
    """Exercise scope authorization separately from query results and persistence."""

    def test_dashboard_listing_policy_is_independent_of_empty_results(self):
        with system_context(reason="tests.dashboard_visibility.scope"):
            empty = self.Queue.objects.create(name="Empty queue", slug="empty", key="EMP", owner=self.manager)
        query = """query($policies: [DashboardWidgetVisibilityInput!]!) {
          dashboard_widget_visibility(policies: $policies)
          project_tasks(where: {queue__slug: {_eq: "empty"}}) { id }
        }"""
        variables = {"policies": [{"resource": "projects.Task", "key": "queue__slug", "value": empty.slug}]}
        visible = self.graphql(query, variables, user=self.manager, bucket="console")
        hidden = self.graphql(query, variables, user=self.outsider, bucket="console")
        self.assertEqual(visible, {"dashboard_widget_visibility": [True], "project_tasks": []})
        self.assertEqual(hidden, {"dashboard_widget_visibility": [False], "project_tasks": []})
        counts = []
        for size in (1, 20):
            with CaptureQueriesContext(connection) as queries:
                answers = self.graphql(query, {"policies": variables["policies"] * size},
                                       user=self.manager, bucket="console")
            self.assertEqual(answers["dashboard_widget_visibility"], [True] * size)
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])

    def test_widget_policy_survives_snapshot_persistence(self):
        dashboard_model = apps.get_model("dashboards", "Dashboard")
        policy = {"resource": "projects.Task", "key": "queue__slug", "value": self.queue.slug}
        snapshot = {"schemaVersion": 1, "columns": 12, "widgets": [{
            "schemaVersion": 1, "kindVersion": 1, "id": "summary", "kind": "authored", "title": "Summary",
            "data": {"shape": "none", "binding": {"dashboardKey": "summary", "widgetId": "summary"}},
            "options": {}, "visibility": policy, "x": 0, "y": 0, "w": 6, "h": 3, "isArchived": False,
        }]}
        with actor_context(self.manager):
            dashboard = dashboard_model.objects.create_personal(
                self.manager, name="Overview", client_creation_key="snapshot",
            )
            updated = dashboard_model.objects.save_snapshot(
                self.manager, scope="personal", scope_key=None, persisted_id=dashboard.pk,
                expected_revision=dashboard.revision, snapshot=snapshot,
            )
            self.assertEqual(updated.snapshot()["widgets"][0]["visibility"], policy)


class ServerVerbCase(ProjectSetupFixture):
    """Requester permissions and partial-success batches in both relationship stores."""

    def approved_request(self):
        project = self.setup_project()
        with actor_context(self.admin):
            self.as_user(self.need, self.admin).decide_access("approve")
        self.need.refresh_from_db()
        return project, self.Round._base_manager.get(project=project)

    def selection(self, *rows):
        return [{"id": str(row.sqid), "expected_revision": row.revision} for row in rows]

    def test_question_widening_keeps_recipient_discussion_and_requester_path_explicit(self):
        """D35 keeps recipients read/comment-only; D38 assigns requester widening to consumers."""
        project, round = self.approved_request()
        self.assertTrue(self.scoped(self.source, self.reader))
        with actor_context(self.manager):
            question = self.as_user(round, self.manager).ask("Question", "Details", recipient=self.member)
        self.assertFalse(self.scoped(question, self.reader))
        self.assertTrue(self.scoped(question, self.member))
        self.assertFalse(self.scoped(question, self.member, "write"))
        with actor_context(self.manager):
            self.as_user(question, self.manager).set_visibility("inherited")
        # A consumer contributes any parent-requester path; the framework
        # proposal fragment grants this widened question to round responders.
        self.assertFalse(self.scoped(question, self.reader))
        self.assertFalse(self.scoped(question, self.reader, "comment"))
        for user in (self.member,):
            self.assertTrue(self.scoped(question, user))
            self.assertTrue(self.scoped(question, user, "comment"))
            for permission in ("write", "share", "delete"):
                self.assertFalse(self.scoped(question, user, permission))
        self.assertTrue(self.scoped(question, self.manager, "write"))
        self.assertFalse(self.scoped(project, self.reader))
        with actor_context(self.manager):
            self.as_user(self.need, self.manager).reset_access(
                confirmed=True, expected_revision=self.need.revision,
            )
        self.assertFalse(self.scoped(question, self.reader))

    def test_accept_and_decline_many_commit_eligible_rows_and_report_stale_denied_ineligible(self):
        for verb, arguments in (("accept_tasks", ""), ("decline_tasks", ", reason: DECLINED")):
            good = self.task("Triage")
            stale = self.task("Triage")
            ineligible = self.task("Doing")
            denied = self.task("Triage", visibility="restricted")
            selection = self.selection(good, stale, ineligible, denied)
            selection[1]["expected_revision"] += 1
            mutation = f"""mutation($selection: [ActionSelectionInput!]!) {{
              {verb}(selection: $selection{arguments}) {{ ok code id }}
            }}"""
            rows = self.graphql(mutation, {"selection": selection}, user=self.manager)[verb]
            self.assertEqual([row["id"] for row in rows], [item["id"] for item in selection])
            self.assertEqual([row["ok"] for row in rows], [True, False, False, False])
            self.assertEqual(rows[1]["code"], "STALE_REVISION")
            good.refresh_from_db()
            stale.refresh_from_db()
            self.assertNotEqual(good.stage_id, self.stages["Triage"].pk)
            self.assertEqual(stale.stage_id, self.stages["Triage"].pk)
            replay = self.graphql(mutation, {"selection": selection}, user=self.manager)[verb]
            self.assertEqual(replay[0]["code"], "STALE_REVISION")

    def test_remove_many_conceals_and_refuses_promoted_tasks(self):
        good = self.task()
        promoted = self.source
        with actor_context(self.manager):
            self.as_user(promoted, self.manager).promote_to_project()
        promoted.refresh_from_db()
        rows = self.graphql("""mutation($selection: [ActionSelectionInput!]!) {
          remove_tasks(selection: $selection) { ok }
        }""", {"selection": self.selection(good, promoted)}, user=self.manager)["remove_tasks"]
        self.assertEqual([row["ok"] for row in rows], [True, False])
        good.refresh_from_db()
        self.assertTrue(good.stage.conceals)
        self.assertTrue(self.Task._base_manager.filter(pk=good.pk).exists())
        with actor_context(self.manager), self.assertRaises(PermissionDenied):
            self.as_user(good, self.manager).accept()

    def test_resolve_many_is_a_manager_operation_with_per_question_revision(self):
        project = self.setup_project()
        round = self.Round._base_manager.get(project=project)
        with actor_context(self.manager):
            first = self.as_user(round, self.manager).ask("First", "Details", recipient=self.member)
            second = self.as_user(round, self.manager).ask("Second", "Details", recipient=self.member)
        mutation = """mutation($selection: [ActionSelectionInput!]!) {
          resolve_proposal_clarifications(selection: $selection) { ok code }
        }"""
        rows = self.graphql(mutation, {"selection": self.selection(first)}, user=self.member)
        self.assertFalse(rows["resolve_proposal_clarifications"][0]["ok"])
        selection = self.selection(first, second, self.source)
        selection[1]["expected_revision"] += 1
        rows = self.graphql(mutation, {"selection": selection}, user=self.manager)
        self.assertEqual([row["ok"] for row in rows["resolve_proposal_clarifications"]], [True, False, False])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "done")
        self.assertEqual(second.status, "open")


class DenormalizedServerVerbCase(ServerVerbCase):
    storage = "denormalized"


class DenormalizedProjectSetupCase(ProjectSetupCase):
    storage = "denormalized"


class DenormalizedProjectAttentionCase(ProjectAttentionCase):
    storage = "denormalized"


class DenormalizedDashboardVisibilityCase(DashboardVisibilityCase):
    storage = "denormalized"


@pytest.mark.parametrize("storage", ("registry", "denormalized"))
@pytest.mark.parametrize(
    "group", ("ProjectSetupCase", "ProjectAttentionCase", "DashboardVisibilityCase", "ServerVerbCase"),
)
def test_project_setup_contracts(tmp_path: Path, storage: str, group: str):
    case = ("Denormalized" if storage == "denormalized" else "") + group
    run_composed_tests(
        tmp_path, f"tests.test_project_setup.{case}", app=("angee.intake", "angee.proposals_work", "angee.dashboards"),
    )
