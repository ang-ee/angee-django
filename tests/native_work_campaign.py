"""Work contracts on emitted models, executed by tests/composed_host.py only."""

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

import tablib
from asgiref.sync import async_to_sync
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import RequestFactory, TransactionTestCase, override_settings
from fastmcp.exceptions import ToolError
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import backend
from rebac.evaluator import evaluator_scope
from rebac.roles import grant

from angee.graphql.schema import GraphQLSchemas
from angee.mcp.server import mcp_server
from angee.resources.entries import ResourceEntry, ResourceGroup


class WorkCampaignTests(TransactionTestCase):
    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        self.Task = apps.get_model("projects", "Task")
        self.Queue = apps.get_model("work", "Queue")
        self.Stage = apps.get_model("work", "Stage")
        self.Relation = apps.get_model("projects", "TaskRelation")
        with system_context(reason="test.work.composed.setup"):
            self.actor = get_user_model().objects.create_user(username="work-operator", password=None)
            self.outsider = get_user_model().objects.create_user(username="work-outsider", password=None)
            grant(actor=self.actor, role="angee/role:admin")
            self.queue = self.Queue.objects.create(key="WORK", name="Work", slug="work", triage_enabled=True)

    def task(self, *, category="unstarted", estimate=None):
        with system_context(reason="test.work.composed.task"):
            stage = self.Stage._base_manager.get(queue=self.queue, category=category)
            return self.Task.objects.create(title="Work item", queue=self.queue, stage=stage, estimate=estimate)

    def execute(self, schema, query, variables, actor):
        request = RequestFactory().post("/graphql/public/")
        request.user = actor
        with actor_context(actor):
            result = schema.execute_sync(
                query, variable_values=variables, context_value=SimpleNamespace(request=request),
            )
        self.assertIsNone(result.errors, result.errors)
        return result.data

    def test_task_tools_write_replay_complete_and_report_refusals_as_an_ordinary_user(self):
        with system_context(reason="test.work.mcp.caller"):
            caller = get_user_model().objects.create_user(username="work-mcp-caller", password=None)
        self.assertFalse(caller.is_staff)
        self.assertFalse(caller.is_superuser)

        def run_tool(tool, **arguments):
            with actor_context(caller):
                return async_to_sync(tool.run)(arguments).structured_content

        # Build every discovered registrar, including the generated readers.
        tools = {tool.name: tool for tool in async_to_sync(mcp_server().list_tools)()}
        with actor_context(caller):
            queue = self.Queue.objects.create(name="Tool queue", slug="tool-queue", key="TOOL", triage_enabled=True)
            ready = apps.get_model("work", "Stage").objects.get(queue=queue, category="unstarted")
        inputs = {
            "title": "Prepare a checklist",
            "queue": queue.sqid,
            "stage": ready.sqid,
            "client_creation_key": "checklist",
        }
        created = run_tool(tools["create_task"], **inputs)
        self.assertEqual(run_tool(tools["create_task"], **inputs), created)
        updated = run_tool(
            tools["update_task"],
            sqid=created["sqid"],
            title="Prepare the checklist",
            expected_revision=created["revision"],
        )
        with self.assertRaises(ToolError):
            run_tool(
                tools["update_task"], sqid=created["sqid"], title="Stale title", expected_revision=created["revision"]
            )
        completed = run_tool(tools["complete_task"], sqid=created["sqid"])
        self.assertEqual(
            completed,
            {
                "ok": True,
                "message": "Task completed.",
                "code": None,
                "validation_errors": None,
                "sqid": created["sqid"],
            },
        )
        with actor_context(caller):
            task = self.Task.objects.get(sqid=created["sqid"])
            self.assertEqual(task.title, updated["title"])
            self.assertEqual(task.status, "done")
        refused = run_tool(tools["accept_task"], sqid=task.sqid, expected_revision=task.revision)
        self.assertFalse(refused["ok"])
        self.assertTrue(refused["message"])
        self.assertTrue(refused["validation_errors"])
        self.assertEqual(set(refused), {"ok", "message", "code", "validation_errors", "sqid"})
        triage = run_tool(tools["create_task"], title="Triage item", queue=queue.sqid)
        accepted = run_tool(
            tools["accept_tasks"],
            selection=[
                {"id": triage["sqid"], "expected_revision": triage["revision"]},
            ],
        )
        self.assertTrue(accepted["result"][0]["ok"])
        self.assertEqual(accepted["result"][0]["sqid"], triage["sqid"])

    def test_every_hand_verb_can_write_existing_zero_after_policy_tightens(self):
        verbs = ("start", "complete", "reopen", "accept", "decline", "drop", "drop_duplicate",
                 "return_to_triage", "mark_duplicate", "snooze")
        for elevated in (False, True):
            for verb in verbs:
                with self.subTest(verb=verb, elevated=elevated):
                    with system_context(reason="test.work.estimate.allow"):
                        self.Queue._base_manager.filter(pk=self.queue.pk).update(estimate_allow_zero=True)
                    task = self.task(category="triage" if verb in {"accept", "decline", "mark_duplicate", "snooze"}
                                     else "completed" if verb == "reopen" else "unstarted", estimate=0)
                    canonical = self.task(estimate=1)
                    with system_context(reason="test.work.estimate.tighten"):
                        self.Queue._base_manager.filter(pk=self.queue.pk).update(estimate_allow_zero=False)
                    elevation = system_context(reason="test.work.elevated_hand") if elevated else nullcontext()
                    with actor_context(self.actor), elevation:
                        task.with_actor(self.actor)
                        if verb in {"drop", "drop_duplicate"}:
                            task.drop("duplicate" if verb == "drop_duplicate" else "obsolete")
                        elif verb == "decline":
                            task.decline("declined")
                        elif verb == "mark_duplicate":
                            task.mark_duplicate(canonical)
                        elif verb == "snooze":
                            task.snooze(datetime(2099, 1, 1, tzinfo=UTC))
                        else:
                            getattr(task, verb)()
                    task.refresh_from_db()
                    self.assertEqual(task.estimate, 0)
                    self.assertEqual(task.stage.category, {
                        "start": "started", "complete": "completed", "reopen": "unstarted", "accept": "unstarted",
                        "decline": "canceled", "drop": "canceled", "drop_duplicate": "duplicate",
                        "return_to_triage": "triage", "mark_duplicate": "duplicate", "snooze": "triage",
                    }[verb])

    def test_missing_duplicate_stage_does_not_create_relation_or_move_links(self):
        source, canonical = self.task(category="triage"), self.task()
        with system_context(reason="test.work.remove_duplicate"):
            self.Stage.objects.filter(queue=self.queue, category="duplicate").delete()
        with actor_context(self.actor):
            source.with_actor(self.actor)
            link = apps.get_model("projects", "Link").objects.create(target=source, url="https://example.com/reference")
        before = (source.stage_id, source.status)
        with actor_context(self.actor), self.assertRaisesMessage(ValidationError, "Queue has no duplicate stage"):
            source.mark_duplicate(canonical)
        source.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual((source.stage_id, source.status), before)
        self.assertEqual(link.target, source)
        self.assertFalse(self.Relation._base_manager.exists())

    def test_task_permission_scopes_compile_to_one_query_in_both_storage_modes(self):
        task = self.task()
        with system_context(reason="test.work.scope.roster"):
            member = get_user_model().objects.create_user(username="work-member", password=None)
            person = apps.get_model("parties", "Person").objects.for_user(member)
            apps.get_model("spaces", "Membership").objects.create(
                group=self.queue, party=person, role="member", is_confirmed=True,
            )
        for mode in ("denormalized", "registry"):
            with self.subTest(mode=mode), override_settings(REBAC_LOCAL_BACKEND_STORAGE=mode):
                call_command("rebac", "sync", verbosity=0)
                with actor_context(member), evaluator_scope():
                    active = backend()
                    active.schema()
                    for action, allowed in (("read", True), ("write", False)):
                        rows = self.Task.objects.with_actor(member).with_action(action).scoped().filter(pk=task.pk)
                        self.assertEqual(list(rows.values_list("pk", flat=True)), [task.pk] if allowed else [])

    def test_start_and_return_actions_enforce_task_write_and_return_errors(self):
        schema = GraphQLSchemas.from_discovery().build("public")
        task = self.task()
        for action, category in (("start_task", "started"), ("return_task_to_triage", "triage")):
            query = f"mutation($id: ID!) {{ {action}(id: $id) {{ ok message id }} }}"
            denied = self.execute(schema, query, {"id": task.sqid}, self.outsider)[action]
            self.assertFalse(denied["ok"])
            permitted = self.execute(schema, query, {"id": task.sqid}, self.actor)[action]
            self.assertTrue(permitted["ok"], permitted)
            self.assertEqual(permitted["id"], task.sqid)
            task.refresh_from_db()
            self.assertEqual(task.stage.category, category)
        result = self.execute(schema, "mutation($id: ID!) { start_task(id: $id) { ok message validation_errors } }",
                              {"id": task.sqid}, self.actor)["start_task"]
        self.assertFalse(result["ok"])
        self.assertIn("Accept", result["validation_errors"]["stage"][0])

    def test_generated_update_refuses_rule_owned_stage(self):
        schema = GraphQLSchemas.from_discovery().build("public")
        with system_context(reason="test.work.reserved_stage"):
            stage = self.Stage.objects.create(queue=self.queue, name="Reserved", category="started", rule_owned=True)
        request = RequestFactory().post("/graphql/public/")
        request.user = self.actor
        for direction in ("enter", "leave"):
            with self.subTest(direction=direction):
                task = self.task()
                target = stage
                if direction == "leave":
                    with system_context(reason="test.work.rule_entry"):
                        task.stage = stage
                        task.save(update_fields=["stage"])
                    target = self.queue.default_stage
                before = task.stage_id
                with actor_context(self.actor):
                    result = schema.execute_sync(
                        "mutation($id: String!, $stage: ID!) { update_project_tasks_by_pk(pk_columns: {id: $id}, "
                        "_set: {stage: $stage}) { id } }", variable_values={"id": task.sqid, "stage": target.sqid},
                        context_value=SimpleNamespace(request=request),
                    )
                self.assertIsNotNone(result.errors)
                self.assertIn("rule-owned", result.errors[0].message)
                task.refresh_from_db()
                self.assertEqual(task.stage_id, before)

    def test_queue_provisioning_flag_is_readable_insertable_but_not_updatable_or_filterable(self):
        schema = GraphQLSchemas.from_discovery().build("public")._schema
        self.assertIn("provision_stages", schema.get_type("WorkQueueType").fields)
        self.assertIn("provision_stages", schema.get_type("work_queues_insert_input").fields)
        self.assertNotIn("provision_stages", schema.get_type("work_queues_set_input").fields)
        self.assertNotIn("provision_stages", schema.get_type("work_queues_bool_exp").fields)

    def test_project_reader_cannot_resolve_an_unreadable_team_relation(self):
        schema = GraphQLSchemas.from_discovery().build("public")
        with actor_context(self.actor):
            team = apps.get_model("spaces", "Group").objects.create(name="Private team", slug="private-team")
            project = apps.get_model("projects", "Project").objects.create(title="Shared project", team=team)
            write_relationships([RelationshipTuple(to_object_ref(project), "reader", to_subject_ref(self.outsider))])
        self.assertTrue(project.with_actor(self.outsider).has_access("read"))
        self.assertFalse(team.with_actor(self.outsider).has_access("read"))
        data = self.execute(
            schema, f'query {{ projects_by_pk(id: "{project.sqid}") {{ id team {{ id }} }} }}', {}, self.outsider,
        )
        self.assertEqual(data["projects_by_pk"], {"id": project.sqid, "team": None})

    def test_resource_loader_authors_all_stages_and_preserves_rule_flags_on_replay(self):
        addon = apps.get_app_config("work")
        queue_entry = ResourceEntry(
            addon=addon, tier="master", source_value="resources/master/campaign.queue.yaml", model="work.Queue",
        )
        stage_entry = ResourceEntry(
            addon=addon, tier="master", source_value="resources/master/campaign.stage.yaml", model="work.Stage",
        )
        queue_rows = tablib.Dataset(
            ["authored_queue", "AUTHORED", "Authored", "authored", False],
            headers=["_xref", "key", "name", "slug", "provision_stages"],
        )
        stage_rows = tablib.Dataset(
            ["incoming", "work.authored_queue", "Incoming", "triage", 1, False],
            ["ready", "work.authored_queue", "Ready", "unstarted", 2, False],
            ["automatic", "work.authored_queue", "Automatic", "started", 3, True],
            headers=["_xref", "queue", "name", "category", "position", "rule_owned"],
        )
        groups = (
            ResourceGroup(queue_entry, "work.Queue", queue_rows, [1]),
            ResourceGroup(stage_entry, "work.Stage", stage_rows, [1, 2, 3]),
        )
        ledger = apps.get_model("resources", "Resource")
        for expected_created in (4, 0):
            result = ledger.objects._import_groups(
                (queue_entry, stage_entry), groups, (), dry_run=False, addon_aliases={"work": "angee.work"},
            )
            self.assertEqual(result.created, expected_created)
            self.assertEqual(result.skipped, 4 - expected_created)
        queue = self.Queue._base_manager.get(key="AUTHORED")
        self.assertFalse(queue.provision_stages)
        self.assertEqual(list(self.Stage._base_manager.filter(queue=queue).values_list("name", "rule_owned")),
                         [("Incoming", False), ("Ready", False), ("Automatic", True)])
        self.assertEqual(self.Stage.resolve_default(queue).name, "Ready")
