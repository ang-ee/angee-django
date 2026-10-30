"""Real eligibility, ACP, workflow and reply owners; fake inference and dispatch."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Barrier
from time import monotonic, sleep
from types import ModuleType, SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

import tablib
import yaml
from asgiref.sync import async_to_sync
from django.apps import AppConfig, apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone
from rebac import (
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.memberships import containers_of
from rebac.roles import grant as grant_role
from rebac.roles import revoke as revoke_role

from angee.agents.context import record_view_context
from angee.agents.grants import (
    builtin_mcp_server,
    sync_builtin_tool_catalogue,
)
from angee.agents.provisioning import provision_agent
from angee.agents.reader_role import RESOURCE_READER_ROLE
from angee.agents.runners import TurnOutcome
from angee.agents.sessions import run_next_turn
from angee.agents.testing.drivers import FakeRunner, update_chunk
from angee.agents_runtime_pydantic.runtime import PydanticAIRuntime
from angee.agents_runtime_pydantic.toolsets import AngeeToolset, ToolGrantAccess
from angee.base.models.identity import instance_from_public_id
from angee.base.testing.packages import write_addon_distribution
from angee.jobs.enqueue import celery_app
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.posts.backends import ParsedPost
from angee.posts.ingest import land_posts
from angee.provenance.context import changeset_model
from angee.resources.entries import ResourceEntry
from angee.resources.loader import build_resource
from angee.resources.tiers import ResourceTier
from angee.resources.widgets import resolve_ledger_xref
from angee.workflows.runner import runner
from angee.workflows.tasks import cancel as cancel_workflow
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.triggers import TriggerSource
from angee.workflows_agents.steps import MAX_PENDING_REDISPATCHES, StartConversation, render_prompt

Agent = apps.get_model("agents", "Agent")
Workflow = apps.get_model("workflows", "Workflow")
AgentSession = apps.get_model("agents", "AgentSession")
AgentTurn = apps.get_model("agents", "AgentTurn")
MCPServer = apps.get_model("agents", "MCPServer")
MCPTool = apps.get_model("agents", "MCPTool")
Feed = apps.get_model("posts", "Feed")
Message = apps.get_model("messaging", "Message")
Trigger = apps.get_model("workflows", "Trigger")
WorkflowRun = apps.get_model("workflows", "WorkflowRun")
StepRun = apps.get_model("workflows", "StepRun")
StepWatch = apps.get_model("workflows", "StepWatch")

READ_TOOLS = {"read_comment_thread", "read_message_text", "search_pages", "list_vaults"}


class ConversationPromptTests(SimpleTestCase):
    def test_prompt_exposes_only_subject_identity_and_bound_json(self):
        context = {"kind": "record", "type": "messaging/message", "sqid": "msg_example"}
        self.assertEqual(
            render_prompt(
                "Answer {subject[sqid]} in {input[language]}.",
                context=context,
                data={"language": "French"},
            ),
            "Answer msg_example in French.",
        )
        for template in ("{subject.channel.credential.secret_value}", "{subject[channel]}", "{run.run_as}"):
            with self.subTest(template=template), self.assertRaises(ValidationError):
                render_prompt(template, context=context, data={})


class CommentReplyTests(TransactionTestCase):
    def setUp(self):
        self.tasks = self.enterContext(patch.object(celery_app, "send_task"))
        call_command("rebac", "sync", verbosity=0)
        self.fake = FakeRunner()
        self.fake.outcome = TurnOutcome(kind="completed", text="A useful answer.")
        self.enterContext(patch.object(PydanticAIRuntime, "session_runner", return_value=self.fake))
        with system_context(reason="comment reply participants"):
            self.admin = get_user_model().objects.create_user(username="reply-admin")
            grant_role(actor=self.admin, role="angee/role:admin")
            self.owner = get_user_model().objects.create_user(username="reply-owner")
            self.other = get_user_model().objects.create_user(username="reply-other")
            self.agent = Agent.objects.create(
                name="Comment assistant",
                owner=self.owner,
                runtime_class="pydantic",
                runtime_status="running",
            )
            vendor = apps.get_model("integrate", "Vendor").objects.create(slug="reply-feed", display_name="Feed")
            handle = apps.get_model("parties", "Handle").objects.create(platform="youtube", value="publisher")
            self.feed = Feed.objects.create(
                vendor=vendor,
                owner=self.owner,
                handle=handle,
                lifecycle="connected",
                feed_backend_class="manual",
                live_since=timezone.now() - timedelta(minutes=1),
            )
            self.channel = apps.get_model("messaging", "Channel").objects.get(pk=self.feed.pk)
            self.post = land_posts(
                self.feed,
                [
                    ParsedPost(
                        ParsedMessage(external_id="post", platform="youtube", sent_at=timezone.now()),
                        is_original_post=True,
                    )
                ],
                owner_id=self.owner.pk,
            )[0]
        with actor_context(self.admin):
            sync_builtin_tool_catalogue()
            self.server = builtin_mcp_server()
            self.agent.mcp_servers.add(self.server)
            for name in sorted(READ_TOOLS):
                self.agent.mcp_tools.add(MCPTool.objects.get(server=self.server, name=name))
            self.channel.grant_record_access("reader", self.agent.user)
            self.feed.handle.grant_record_access("reader", self.agent.user)
        with actor_context(self.owner):
            self.vault = apps.get_model("knowledge", "Vault").objects.create_for(self.owner, name="Reply references")
            self.vault.grant_record_access("viewer", self.agent.user)
        self.workflow = self.make_workflow("comment-reply")
        with actor_context(self.admin):
            trigger = Trigger.objects.create(
                workflow=self.workflow,
                source="message_ingested",
                channel=self.feed,
                condition={"direction": {"_eq": "inbound"}, "is_original_post": {"_eq": False}},
            )
            Trigger.objects.enable(trigger, actor=self.admin)
        with system_context(reason="public comment ingest"):
            self.comment = land_posts(
                self.feed,
                [
                    ParsedPost(
                        ParsedMessage(
                            external_id="comment",
                            platform="youtube",
                            in_reply_to="post",
                            sent_at=timezone.now(),
                            sender=ParsedHandle(platform="youtube", value="author"),
                            body=ParsedPart(type="text/plain", role="body", text="Can you help?"),
                        )
                    )
                ],
                owner_id=self.owner.pk,
            )[0]

    def document(self):
        return {
            "nodes": {
                "check": {"step": "check_replied", "next": {"open": "converse"}},
                "converse": {
                    "step": "start_conversation",
                    "input": {"from": "input", "project": True},
                    "config": {"agent": self.agent.sqid, "prompt_template": "Prepare an answer for {subject[sqid]}."},
                    "next": {"done": "schedule", "no_reply": "close"},
                },
                "schedule": {
                    "step": "schedule_reply",
                    "input": {"from": "converse"},
                    "next": {"scheduled": "close", "replied": "close"},
                },
                "close": {"step": "close_conversation", "input": {"from": "converse"}},
            },
            "results": [
                {"from": "check", "when": ["replied"]},
                {"from": "schedule", "when": ["scheduled"]},
                {"from": "schedule", "when": ["replied"]},
                {"from": "converse", "when": ["no_reply"]},
            ],
        }

    def make_workflow(self, key, *, can_reply=True):
        workflow = load_workflow(self.document(), actor=self.admin, key=key, subject_model="messaging.Message")
        with actor_context(self.owner):
            self.agent.with_actor(self.owner).grant_record_access("caller", workflow.user)
            self.channel.grant_record_access("reader", workflow.user)
            if can_reply:
                self.channel.grant_record_access("replier", workflow.user)
        return workflow

    def step(self, run, node="converse"):
        return StepRun.system_queryset().get(run=run, node_key=node)

    def waiting_run(self):
        self.assertEqual(Trigger.objects.drain(), 1)
        run = WorkflowRun.system_queryset().get(subject_object_id=self.comment.pk)
        run_until(run)
        step = self.step(run)
        self.assertEqual(
            (self.step(run, "check").outcome, step.status, step.waiting_kind),
            ("open", "waiting", "record"),
            step.failure_reason,
        )
        self.assertIsNotNone(step.wake_at)
        self.assertEqual(step.attempt, 1)
        session = instance_from_public_id(AgentSession, step.state["session"], queryset=AgentSession.system_queryset())
        self.assertEqual(AgentTurn.system_queryset().filter(session=session).count(), 1)
        return run, session

    def complete(self, run, session, *, node=None):
        run_next_turn(session.pk)
        self.assertEqual(runner.wake_records(), 1)
        return run_until(run, node=node)

    def expire(self, run):
        step = self.step(run)
        past = timezone.now() - timedelta(seconds=1)
        StepRun.system_queryset().filter(pk=step.pk).update(
            state={**step.state, "deadline": past.isoformat()},
            wake_at=past,
        )
        self.assertEqual(runner.wake(), 1)
        return run_until(run)

    def replies(self):
        return Message.system_queryset().filter(parent=self.comment, direction="outbound")

    def test_open_comment_runs_a_turn_then_prepares_an_approval_only_reply(self):
        run, session = self.waiting_run()
        self.complete(run, session)
        self.assertEqual((run.run_as_id, run.status, run.outcome), (self.workflow.user_id, "succeeded", "scheduled"))
        turn = AgentTurn.system_queryset().get(session=session)
        self.assertEqual(self.step(run).output["text"], turn.text)
        reply = self.replies().get()
        self.assertEqual(reply.metadata["local"]["agent_turn"], turn.sqid)
        self.assertEqual((run.output["message_id"], run.output["status"]), (reply.sqid, "draft"))
        self.assertIsNone(reply.scheduled_at)
        claim = changeset_model()._base_manager.get(
            scope=f"messaging.reply:{to_subject_ref(self.workflow.user)}",
            key=f"workflow-reply:{run.sqid}",
        )
        self.assertEqual(claim.created_row.pk, reply.pk)
        self.assertIn("approval", self.step(run, "schedule").notes[0]["message"])
        self.assertEqual(AgentSession.system_queryset().get(pk=session.pk).status, "closed")
        self.assertFalse(StepWatch.system_queryset().filter(step_run__run=run).exists())
        self.assertTrue(
            apps.get_model("workflows", "StepRecord").system_queryset()
            .filter(
                run=run,
                label="Reply",
                operation="created",
                object_id=reply.pk,
            )
            .exists()
        )

    def test_resource_loader_installs_a_publisherless_comment_reply_workflow(self):
        ledger = apps.get_model("resources", "Resource")
        agents = apps.get_app_config("agents")
        with system_context(reason="installed reply reference"):
            ledger.objects.bind_instance(
                addon=agents,
                xref="installed_reply_agent",
                instance=self.agent,
                source="agents",
            )
        document = self.document()
        document["nodes"]["converse"]["config"]["agent"] = "agents.installed_reply_agent"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_addon_distribution(root , 'tests.reply_install', depends_on=())
            (root / "workflow.yaml").write_text(
                yaml.safe_dump(
                    {
                        "_meta": {"model": "workflows.Workflow"},
                        "rows": [
                            {
                                "xref": "reply",
                                "fields": {
                                    "key": "installed-comment-reply",
                                    "name": "Installed comment reply",
                                    "subject_model": "messaging.Message",
                                    "publish": True,
                                    "draft": document,
                                },
                            }
                        ],
                    }
                )
            )
            module = ModuleType("tests.reply_install")
            module.__file__ = str(root / "__init__.py")
            addon = AppConfig(module.__name__, module)
            ledger.objects.load_addons((agents, addon), tiers=["install"])
        with system_context(reason="installed publication"):
            workflow = Workflow.objects.get(key="installed-comment-reply")
            self.assertIsNone(workflow.published.published_by_id)
        with actor_context(self.owner):
            self.agent.with_actor(self.owner).grant_record_access("caller", workflow.user)
            self.channel.grant_record_access("reader", workflow.user)
            self.channel.grant_record_access("replier", workflow.user)
        run = start_run(workflow, actor=workflow.user, subject=self.comment)
        run_until(run)
        self.assertEqual(self.step(run).status, "waiting", self.step(run).failure_reason)
        session = instance_from_public_id(
            AgentSession, self.step(run).state["session"], queryset=AgentSession.system_queryset()
        )
        self.assertEqual(AgentTurn.system_queryset().get(session=session).status, "pending")
        self.complete(run, session)
        self.assertEqual((run.status, run.outcome), ("succeeded", "scheduled"))

    def test_agent_editor_cannot_grant_caller_to_themselves_or_anyone_else(self):
        with system_context(reason="agent call delegation fixture"):
            editor = get_user_model().objects.create_user(username="reply-editor")
            write_relationships(
                [
                    RelationshipTuple(
                        resource=to_object_ref(self.agent),
                        relation="editor",
                        subject=to_subject_ref(editor),
                    ),
                ]
            )
        agent = self.agent.with_actor(editor)
        self.assertTrue(agent.has_access("write"))
        self.assertTrue(agent.has_access("share"))
        self.assertFalse(agent.has_access("call"))
        for recipient in (editor, self.other):
            with self.subTest(recipient=recipient.username):
                with self.assertRaises(PermissionDenied):
                    agent.with_actor(editor).grant_record_access("caller", recipient)
                self.assertFalse(agent.with_actor(recipient).has_access("call"))

    def test_agent_owner_and_admin_can_grant_caller(self):
        for actor in (self.owner, self.admin):
            with self.subTest(actor=actor.username):
                self.assertFalse(self.agent.with_actor(self.other).has_access("call"))
                self.agent.with_actor(actor).grant_record_access("caller", self.other)
                self.assertTrue(self.agent.with_actor(self.other).has_access("call"))
                self.agent.with_actor(actor).revoke_record_access("caller", self.other)

    def test_cancelled_run_closes_created_session_and_stops_pending_inference(self):
        run, session = self.waiting_run()
        WorkflowRun.objects.cancel(run, actor=self.admin)
        run.refresh_from_db()
        self.assertEqual(run.status, "canceled")
        self.assertEqual(AgentSession.system_queryset().get(pk=session.pk).status, "closed")
        self.assertEqual(AgentTurn.system_queryset().get(session=session).status, "canceled")
        run_next_turn(session.pk)
        self.assertEqual(self.fake.prompts, [])

    @skipUnless(connection.vendor == "postgresql", "Independent PostgreSQL locks required.")
    def test_cancellation_waits_for_settlement_before_removing_turn_watches(self):
        run, session = self.waiting_run()
        turn = session.claim_turn()
        started = Queue()

        def cancel():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    started.put(cursor.fetchone()[0])
                return WorkflowRun.objects.cancel(run, actor=self.admin)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                AgentSession.system_queryset(lock=("self",)).get(pk=session.pk)
                contender = pool.submit(copy_context().run, cancel)
                pid = started.get(timeout=10)
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_stat_clear_snapshot()")
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
                        if cursor.fetchone() == ("Lock",):
                            break
                    self.assertFalse(contender.done(), "Cancellation finished before waiting for the session.")
                    sleep(0.01)
                else:
                    self.fail("Cancellation did not reach a PostgreSQL row-lock wait.")
                # The worker owns the session; its turn save must still update the watches.
                session.settle_turn(turn, TurnOutcome(kind="completed", text="Settled before cancellation"))
            self.assertTrue(contender.result(timeout=10).canceled)
        run.refresh_from_db()
        turn.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual((run.status, session.status, turn.status), ("canceled", "closed", "completed"))
        self.assertFalse(StepWatch.system_queryset().filter(step_run__run=run).exists())

    def test_cancellation_of_a_continued_session_stops_only_the_runs_turn(self):
        session = AgentSession.objects.start(
            self.agent,
            user=self.workflow.user,
            context=record_view_context(self.comment),
            actor=self.workflow.user,
        )
        run = start_run(self.workflow, actor=self.workflow.user, subject=self.comment, input={"session": session.sqid})
        run_until(run)
        self.assertEqual(self.step(run).status, "waiting", self.step(run).failure_reason)
        turn = session.claim_turn()
        other = session.post("Another session user's queued turn", context={}, actor=self.admin)
        WorkflowRun.objects.cancel(run, actor=self.admin)
        session.refresh_from_db()
        turn.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((session.status, turn.status, other.status), ("idle", "canceled", "pending"))

    def test_deleting_running_conversations_and_turns_still_requires_stop_first(self):
        run, session = self.waiting_run()
        turn = session.claim_turn()
        self.tasks.reset_mock()
        for model, record in ((AgentSession, session), (AgentTurn, turn)):
            with self.subTest(model=model), system_context(reason="active workflow record deletion guard"):
                with self.assertRaisesMessage(ValidationError, "stop it first"):
                    model.system_queryset().filter(pk=record.pk).delete()
                session.refresh_from_db()
                turn.refresh_from_db()
                run.refresh_from_db()
                self.assertEqual((run.status, session.status, turn.status), ("waiting", "running", "running"))
                self.assertFalse(any(call.args[0] == "workflows.cancel" for call in self.tasks.call_args_list))

    def test_deleted_subject_closes_session_when_deferred_cancellation_is_delivered(self):
        run, session = self.waiting_run()
        self.tasks.reset_mock()
        with system_context(reason="deleted reply subject"):
            Message.system_queryset().filter(pk=self.comment.pk).delete()
        self.assertEqual(AgentSession.system_queryset().get(pk=session.pk).status, "idle")
        self.assertEqual(AgentTurn.system_queryset().get(session=session).status, "pending")
        deliveries = [call.kwargs["kwargs"] for call in self.tasks.call_args_list if call.args[0] == "workflows.cancel"]
        self.assertEqual(len(deliveries), 1)
        cancel_workflow(**deliveries[0])
        self.assertEqual(AgentSession.system_queryset().get(pk=session.pk).status, "closed")
        self.assertEqual(AgentTurn.system_queryset().get(session=session).status, "canceled")
        run_next_turn(session.pk)
        self.assertEqual(self.fake.prompts, [])

    def test_existing_held_reply_ends_before_opening_a_session(self):
        with actor_context(self.owner):
            existing = self.comment.reply_to_comment(
                body="Already answered.",
                actor=self.owner,
                idempotency_key="existing",
            )
        self.assertEqual(Trigger.objects.drain(), 1)
        run = WorkflowRun.system_queryset().get(subject_object_id=self.comment.pk)
        run_until(run)
        self.assertEqual((run.status, run.outcome), ("succeeded", "replied"))
        self.assertIn("already answered", self.step(run, "check").notes[0]["message"])
        self.assertEqual(self.replies().get().pk, existing.pk)
        self.assertFalse(AgentSession.system_queryset().exists())
        self.assertEqual(self.fake.prompts, [])

    def test_reprocessing_an_answered_comment_ends_without_another_turn(self):
        run, session = self.waiting_run()
        self.complete(run, session)
        again = start_run(self.workflow, actor=self.workflow.user, subject=self.comment)
        run_until(again)
        self.assertEqual(again.outcome, "replied")
        self.assertEqual(self.replies().count(), 1)
        self.assertEqual(AgentTurn.system_queryset().count(), 1)

    def native_answer(self):
        with system_context(reason="native channel answer"):
            land_posts(
                self.feed,
                [
                    ParsedPost(
                        ParsedMessage(
                            external_id="native-answer",
                            platform="youtube",
                            in_reply_to="comment",
                            sent_at=timezone.now(),
                            sender=ParsedHandle(platform="youtube", value="publisher"),
                            body=ParsedPart(text="Native answer"),
                        )
                    )
                ],
                owner_id=self.owner.pk,
            )

    def test_native_answer_during_inference_settles_replied_and_closes_the_session(self):
        run, session = self.waiting_run()
        self.fake.during_turn = lambda *args: self.native_answer()
        self.complete(run, session)
        self.assertEqual((run.status, run.outcome, run.output), ("succeeded", "replied", {}))
        self.assertEqual(self.step(run, "schedule").output, {})
        self.assertFalse(self.replies().exists())
        self.assertEqual(AgentSession.system_queryset().get(pk=session.pk).status, "closed")

    def test_schedule_replay_precedes_answer_admission(self):
        run, session = self.waiting_run()
        self.complete(run, session, node="schedule")
        turn = AgentTurn.system_queryset().get(session=session)
        existing = self.comment.reply_to_comment(
            body=turn.text,
            actor=self.workflow.user,
            local={"agent_turn": turn.sqid},
            idempotency_key=f"workflow-reply:{run.sqid}",
        )
        self.native_answer()
        with patch.object(Message, "reply_state", side_effect=AssertionError("Replay must precede admission")):
            runner.execute(self.step(run, "schedule").pk)
            run_until(run)
        self.assertEqual((run.status, run.outcome, run.output["message_id"]), ("succeeded", "scheduled", existing.sqid))
        self.assertEqual(self.replies().count(), 1)

    def test_empty_or_whitespace_answer_closes_without_scheduling(self):
        for index, text in enumerate(("", " \n\t ")):
            with self.subTest(text=text):
                workflow = self.make_workflow(f"empty-answer-{index}")
                run = start_run(workflow, actor=workflow.user, subject=self.comment)
                run_until(run)
                session = instance_from_public_id(
                    AgentSession, self.step(run).state["session"], queryset=AgentSession.system_queryset()
                )
                self.fake.outcome = TurnOutcome(kind="completed", text=text)
                self.complete(run, session)
                self.assertEqual((run.status, run.outcome, run.output["text"]), ("succeeded", "no_reply", text))
                self.assertEqual(self.step(run, "schedule").status, "skipped")
                self.assertEqual(AgentSession.system_queryset().get(pk=session.pk).status, "closed")
        self.assertFalse(self.replies().exists())

    def test_schedule_redelivery_and_idempotency_replay_keep_one_reply(self):
        run, session = self.waiting_run()
        self.complete(run, session, node="schedule")
        schedule = self.step(run, "schedule")
        runner.execute(schedule.pk)
        runner.execute(schedule.pk)
        run_until(run)
        reply = self.replies().get()
        turn = AgentTurn.system_queryset().get(session=session)
        with actor_context(self.owner):
            self.feed.reply_hold = 0
            self.feed.save(update_fields=["reply_hold"])
        with actor_context(self.workflow.user):
            replay = self.comment.reply_to_comment(
                body=turn.text,
                actor=self.workflow.user,
                local={"agent_turn": turn.sqid},
                idempotency_key=f"workflow-reply:{run.sqid}",
            )
        self.assertEqual((replay.pk, replay.status, replay.scheduled_at), (reply.pk, "draft", None))
        self.assertEqual(self.replies().count(), 1)

    def test_concurrent_schedule_deliveries_create_one_reply(self):
        run, session = self.waiting_run()
        self.complete(run, session, node="schedule")
        schedule = self.step(run, "schedule")
        barrier = Barrier(2) if connection.vendor == "postgresql" else None

        def deliver():
            close_old_connections()
            try:
                if barrier is not None:
                    barrier.wait(timeout=15)
                runner.execute(schedule.pk)
            finally:
                close_old_connections()

        if barrier is None:
            deliver()
            deliver()
        else:
            with ThreadPoolExecutor(max_workers=2) as workers:
                list(workers.map(lambda _: deliver(), range(2)))
        run_until(run)
        self.assertEqual((run.status, run.outcome), ("succeeded", "scheduled"))
        self.assertEqual(self.replies().count(), 1)

    def test_schedule_retry_retains_the_key_and_rolls_back_failed_body_writes(self):
        run, session = self.waiting_run()
        self.complete(run, session, node="schedule")
        original = Message.reply_to_comment
        keys = []

        def prepare(comment, **kwargs):
            keys.append(kwargs["idempotency_key"])
            reply = original(comment, **kwargs)
            if len(keys) == 1:
                raise ValidationError("Interrupted before workflow settlement.")
            return reply

        with patch.object(Message, "reply_to_comment", prepare):
            runner.execute(self.step(run, "schedule").pk)
            self.assertFalse(self.replies().exists())
            StepRun.objects.retry_step(self.step(run, "schedule"), actor=self.admin)
            run_until(run)
        self.assertEqual(keys, [f"workflow-reply:{run.sqid}"] * 2)
        self.assertEqual((run.status, self.replies().count()), ("succeeded", 1))

    def test_operator_retry_opens_a_session_after_failed_run_cleanup(self):
        run, session = self.waiting_run()
        self.fake.outcome = TurnOutcome(kind="failed", error="Temporary provider failure.")
        self.complete(run, session)
        self.assertEqual(run.status, "failed")
        self.assertFalse(self.replies().exists())
        StepRun.objects.retry_step(self.step(run), actor=self.admin)
        run_until(run)
        self.assertEqual(AgentSession.system_queryset().get(pk=session.pk).status, "closed")
        self.assertEqual(AgentTurn.system_queryset().filter(session=session).count(), 1)
        self.assertEqual(AgentSession.system_queryset().count(), 2)
        session = instance_from_public_id(
            AgentSession, self.step(run).state["session"], queryset=AgentSession.system_queryset()
        )
        self.fake.outcome = TurnOutcome(kind="completed", text="Recovered answer.")
        self.complete(run, session)
        self.assertEqual((run.outcome, self.replies().count()), ("scheduled", 1))

    def test_transcript_flushes_do_not_wake_or_create_attempts(self):
        run, session = self.waiting_run()
        turn = session.claim_turn()
        self.assertEqual(runner.wake_records(), 1)
        run_until(run)
        before = self.step(run).attempt
        self.tasks.reset_mock()
        with actor_context(self.agent.principal_subject()):
            for text in ("First", "Second", "Third", "Final"):
                self.assertTrue(turn.append_updates([update_chunk(text)]))
                self.assertEqual(runner.wake_records(), 0)
        self.assertEqual(self.step(run).attempt, before)
        self.assertFalse(any(call.args[0] == "workflows.wake_records" for call in self.tasks.call_args_list))
        session.settle_turn(turn, TurnOutcome(kind="completed", text="Final"))
        self.assertEqual(runner.wake_records(), 1)
        run_until(run)
        self.assertEqual(self.step(run).attempt, before + 1)
        self.assertEqual(self.step(run).attempt, 3)

    def test_deadline_redispatches_pending_turn_boundedly_then_cancels(self):
        run, session = self.waiting_run()
        self.tasks.reset_mock()
        for expected in range(1, MAX_PENDING_REDISPATCHES + 1):
            self.expire(run)
            self.assertEqual(self.step(run).state["redispatches"], expected)
            self.assertEqual(AgentTurn.system_queryset().get(session=session).status, "pending")
        self.expire(run)
        self.assertEqual(run.status, "failed")
        self.assertIn("deadline", self.step(run).failure_reason)
        self.assertEqual(AgentTurn.system_queryset().get(session=session).status, "canceled")
        self.assertEqual(
            sum(call.args[0] == "agents.run_session" for call in self.tasks.call_args_list), MAX_PENDING_REDISPATCHES
        )

    def test_running_turn_deadline_cancellation_survives_failed_body_rollback(self):
        run, session = self.waiting_run()
        session.claim_turn()
        self.assertEqual(runner.wake_records(), 1)
        run_until(run)
        self.expire(run)
        self.assertEqual(run.status, "failed")
        self.assertEqual(AgentTurn.system_queryset().get(session=session).status, "canceled")

    def test_claim_after_queue_backlog_starts_a_fresh_execution_deadline(self):
        run, session = self.waiting_run()
        self.tasks.reset_mock()
        self.expire(run)
        self.expire(run)
        session.claim_turn()
        # Simulate a claim immediately before the last queue deadline expires.
        self.expire(run)
        state = self.step(run).state
        self.assertEqual(self.step(run).status, "waiting")
        self.assertEqual(AgentTurn.system_queryset().get(session=session).status, "running")
        self.assertIsNotNone(state["running_since"])
        self.assertGreater(datetime.fromisoformat(state["deadline"]), timezone.now())
        before = state["deadline"]
        runner.wake_records()
        run_until(run)
        self.assertEqual(self.step(run).state["deadline"], before)
        self.assertEqual(sum(call.args[0] == "agents.run_session" for call in self.tasks.call_args_list), 2)

    def test_first_observed_claim_on_approval_starts_a_fresh_execution_deadline(self):
        run, session = self.waiting_run()
        turn = session.claim_turn()
        with system_context(reason="coalesced running and approval wakes"), transaction.atomic():
            AgentSession.system_queryset(lock=("self",)).get(pk=session.pk)
            turn.mark_awaiting_approval()
            session.mark_awaiting_approval()
        self.expire(run)
        state = self.step(run).state
        self.assertEqual(self.step(run).status, "waiting")
        self.assertIsNotNone(state["running_since"])
        self.assertGreater(datetime.fromisoformat(state["deadline"]), timezone.now())
        turn.refresh_from_db()
        self.assertEqual(turn.status, "awaiting_approval")
        self.expire(run)
        turn.refresh_from_db()
        self.assertEqual((run.status, turn.status), ("failed", "canceled"))

    def test_repeated_wakes_keep_one_unexpired_pending_delivery(self):
        run, session = self.waiting_run()
        first = [call for call in self.tasks.call_args_list if call.args[0] == "agents.run_session"][-1]
        first_expiry = datetime.fromisoformat(self.step(run).state["deadline"])
        self.assertEqual(first.kwargs["expires"], first_expiry)
        original = StartConversation.run
        now = first_expiry + timedelta(seconds=1)

        def after_delivery_expiry(step, ctx):
            ctx.now = now
            return original(step, ctx)

        for _ in range(MAX_PENDING_REDISPATCHES):
            self.tasks.reset_mock()
            with system_context(reason="repeat pending turn observation"):
                turn = AgentTurn.system_queryset().get(session=session)
                turn.save(update_fields=["status"])
            self.assertEqual(runner.wake_records(), 1)
            with patch.object(StartConversation, "run", after_delivery_expiry):
                runner.execute(self.step(run).pk)
            deliveries = [call for call in self.tasks.call_args_list if call.args[0] == "agents.run_session"]
            self.assertEqual(len(deliveries), 1)
            expiry = deliveries[0].kwargs["expires"]
            self.assertLessEqual(first_expiry, now)
            self.assertGreater(expiry, now)
            self.assertEqual(expiry, datetime.fromisoformat(self.step(run).state["deadline"]))
            # Repeated observations inside the same lease do not enqueue another copy.
            self.tasks.reset_mock()
            with system_context(reason="repeat unexpired pending delivery"):
                turn.save(update_fields=["status"])
            self.assertEqual(runner.wake_records(), 1)
            with patch.object(StartConversation, "run", after_delivery_expiry):
                runner.execute(self.step(run).pk)
            self.assertFalse(any(call.args[0] == "agents.run_session" for call in self.tasks.call_args_list))
            first_expiry = expiry
            now = expiry + timedelta(seconds=1)

    def test_catalogue_sync_errors_leave_provisioning_state_unchanged(self):
        ledger = apps.get_model("resources", "Resource")
        for failure in ("external_server", "stale_ledger"):
            with self.subTest(failure=failure), system_context(reason="catalogue sync failure fixture"):
                before = Agent.system_queryset().get(pk=self.agent.pk)
                if failure == "external_server":
                    self.server.config = {}
                    self.server.save(update_fields=["config"])
                else:
                    other = MCPServer.objects.create(name="Other server")
                    ledger.objects.filter(source_addon="angee.agents", xref="mcp_angee").update(target_id=other.sqid)
                with actor_context(self.admin):
                    result = provision_agent(self.agent.sqid)
                self.assertFalse(result.ok)
                self.assertIn("external server" if failure == "external_server" else "another record", result.message)
                after = Agent.system_queryset().get(pk=self.agent.pk)
                self.assertEqual(
                    (after.lifecycle, after.runtime_status, after.last_error, after.updated_at),
                    (before.lifecycle, before.runtime_status, before.last_error, before.updated_at),
                )
                self.server.config = {"builtin": "angee"}
                self.server.save(update_fields=["config"])

    def test_agent_has_only_read_tools_and_cannot_reply_or_edit_the_feed(self):
        channel = self.channel.with_actor(self.agent.user)
        self.assertTrue(channel.has_access("read"))
        for permission in ("reply", "write"):
            self.assertFalse(channel.has_access(permission))
        self.assertFalse(self.feed.with_actor(self.agent.user).has_access("write"))
        self.assertFalse(self.vault.with_actor(self.agent.user).has_access("write"))
        with actor_context(self.agent.user), self.assertRaises(PermissionDenied):
            self.comment.reply_to_comment(body="Unauthorized", actor=self.agent.user, idempotency_key="agent-reply")
        run, session = self.waiting_run()

        def inspect_tools(current, turn, emit):
            toolset = AngeeToolset(current, ToolGrantAccess(self.agent.principal_subject()), str(self.server.sqid))
            tools = async_to_sync(toolset.get_tools)(SimpleNamespace(max_retries=0))
            self.assertEqual(set(tools), READ_TOOLS)
            self.assertTrue(all(tool.registered_tool.annotations.readOnlyHint for tool in tools.values()))

        self.fake.during_turn = inspect_tools
        self.complete(run, session)
        self.assertEqual(run.outcome, "scheduled")

    def test_scheduling_requires_workflow_channel_reply_authority(self):
        workflow = self.make_workflow("reader-only-workflow", can_reply=False)
        run = start_run(workflow, actor=workflow.user, subject=self.comment)
        run_until(run)
        session = instance_from_public_id(
            AgentSession, self.step(run).state["session"], queryset=AgentSession.system_queryset()
        )
        self.complete(run, session)
        self.assertEqual(run.status, "failed")
        self.assertFalse(self.replies().exists())
        self.assertEqual(AgentSession.system_queryset().get(pk=session.pk).status, "closed")

    def test_publisher_losing_call_authority_fails_readably(self):
        run, session = self.waiting_run()
        with system_context(reason="revoke publisher delegation"):
            revoke_role(actor=self.admin, role="angee/role:admin")
        self.complete(run, session)
        self.assertEqual(run.status, "failed")
        self.assertIn("publisher cannot delegate", self.step(run).failure_reason)

    def test_caller_grants_only_agent_call_authority(self):
        with actor_context(self.owner):
            self.agent.with_actor(self.owner).grant_record_access("caller", self.other)
        called = self.agent.with_actor(self.other)
        self.assertTrue(called.has_access("call"))
        for permission in ("read", "write", "share", "delete"):
            self.assertFalse(called.has_access(permission))

    def test_turn_watch_does_not_contribute_a_trigger_choice(self):
        self.assertFalse(any(source.model_label == "agents.AgentTurn" for source in TriggerSource.registered()))
        self.waiting_run()

    def test_positive_hold_is_reported_from_the_message(self):
        with actor_context(self.owner):
            self.feed.reply_hold = 2
            self.feed.save(update_fields=["reply_hold"])
        run, session = self.waiting_run()
        self.complete(run, session)
        reply = self.replies().get()
        self.assertEqual((run.output["status"], reply.status), ("draft", "draft"))
        self.assertEqual(datetime.fromisoformat(run.output["scheduled_at"]), reply.scheduled_at)
        self.assertIn("scheduled for", self.step(run, "schedule").notes[0]["message"])

    def test_zero_hold_reports_queued_delivery(self):
        with actor_context(self.owner):
            self.feed.reply_hold = 0
            self.feed.save(update_fields=["reply_hold"])
        run, session = self.waiting_run()
        self.complete(run, session)
        reply = self.replies().get()
        self.assertEqual((run.output["status"], reply.status), ("queued", "queued"))
        self.assertIsNone(reply.scheduled_at)
        self.assertEqual(self.step(run, "schedule").notes[0]["message"], "Reply queued for delivery.")

    def test_foreign_run_cannot_schedule_another_runs_turn(self):
        run, session = self.waiting_run()
        self.complete(run, session)
        workflow = load_workflow(
            {"nodes": {"schedule": {"step": "schedule_reply"}}, "results": [{"from": "schedule"}]},
            actor=self.admin,
            key="foreign-scheduling",
            subject_model="messaging.Message",
        )
        foreign = start_run(workflow, actor=self.admin, subject=self.comment, input=self.step(run).output)
        run_until(foreign)
        self.assertEqual(foreign.status, "failed")
        self.assertEqual(self.replies().count(), 1)

    def test_session_admission_allows_unrelated_agent_foreign_key_inserts(self):
        self.waiting_run()
        with system_context(reason="additional server fixture"):
            server = MCPServer.objects.create(name="Another server")

        def select_server():
            close_old_connections()
            try:
                with actor_context(self.admin):
                    self.agent.with_actor(self.admin).mcp_servers.add(server)
            finally:
                close_old_connections()

        if connection.vendor == "postgresql":
            with ThreadPoolExecutor(max_workers=1) as worker:
                with transaction.atomic():
                    AgentSession.objects.start(
                        self.agent,
                        user=self.workflow.user,
                        context={},
                        actor=self.workflow.user,
                    )
                    worker.submit(copy_context().run, select_server).result(timeout=10)
        else:
            with transaction.atomic(), actor_context(self.admin):
                AgentSession.objects.start(self.agent, user=self.workflow.user, context={}, actor=self.workflow.user)
                self.agent.with_actor(self.admin).mcp_servers.add(server)
        with system_context(reason="agent server selection assertion"):
            self.assertTrue(self.agent.mcp_servers.filter(pk=server.pk).exists())

    def test_catalogue_sync_provisions_and_adopts_without_demo_rows(self):
        self.assertEqual(MCPServer.system_queryset().count(), 1)
        sync_builtin_tool_catalogue()
        self.assertEqual(builtin_mcp_server().pk, self.server.pk)
        self.assertEqual(
            set(
                MCPTool.system_queryset().filter(server=self.server, name__in=READ_TOOLS).values_list("name", flat=True)
            ),
            READ_TOOLS,
        )
        self.assertFalse(MCPTool.system_queryset().filter(name="reply_to_comment").exists())

    def test_resource_document_publishes_an_agent_xref_without_rewriting_its_draft(self):
        with actor_context(self.admin):
            apps.get_model("resources", "Resource").objects.bind_instance(
                addon=apps.get_app_config("agents"),
                xref="reply_agent",
                instance=self.agent,
                source="participants",
            )
            document = self.document()
            document["nodes"]["converse"]["config"]["agent"] = "agents.reply_agent"
            workflow = Workflow.objects.install_definition(
                key="xref-reply",
                name="Referenced reply",
                subject_model="messaging.Message",
                draft=document,
                actor=self.admin,
            )
            self.assertEqual(workflow.published.document["nodes"]["converse"]["config"]["agent"], self.agent.sqid)

    def test_config_xrefs_use_the_explicit_publisher_without_ambient_authority(self):
        ledger = apps.get_model("resources", "Resource")
        with system_context(reason="publishing reference identities"):
            ledger.objects.bind_instance(
                addon=apps.get_app_config("agents"),
                xref="publisher_agent",
                instance=self.agent,
                source="participants",
            )
        document = self.document()
        document["nodes"]["converse"]["config"]["agent"] = "agents.publisher_agent"
        # The unrelated ambient actor must not replace the explicit publisher.
        with actor_context(self.other):
            workflow = Workflow.objects.install_definition(
                key="explicit-xref",
                name="Explicit publisher",
                draft=document,
                subject_model="messaging.Message",
                actor=self.admin,
            )
        self.assertEqual(workflow.published.document["nodes"]["converse"]["config"]["agent"], self.agent.sqid)
        resolved, issues = Workflow.objects._resolved_document(document, self.admin)
        self.assertFalse(issues)
        self.assertEqual(resolved["nodes"]["converse"]["config"]["agent"], self.agent.sqid)
        _, issues = Workflow.objects._resolved_document(document, self.other)
        self.assertTrue(any(issue.code == "config_reference" for issue in issues))
        with patch.object(
            ledger.objects.__class__,
            "resolve_config_reference",
            side_effect=PermissionDenied("No read"),
        ):
            _, issues = Workflow.objects._resolved_document(document, self.admin)
        self.assertTrue(any(issue.code == "config_reference" and "No read" in issue.message for issue in issues))

    def test_map_body_config_references_resolve_through_the_definition_walk(self):
        ledger = apps.get_model("resources", "Resource")
        with system_context(reason="map body reference identity"):
            ledger.objects.bind_instance(
                addon=apps.get_app_config("agents"),
                xref="map_agent",
                instance=self.agent,
                source="participants",
            )
        document = {
            "nodes": {
                "mapped": {
                    "step": "map",
                    "body": {
                        "step": "start_conversation",
                        "config": {"agent": "agents.map_agent", "prompt_template": "Read {subject[sqid]}."},
                    },
                }
            }
        }
        resolved, issues = Workflow.objects._resolved_document(document, self.admin)
        self.assertFalse(issues)
        self.assertEqual(resolved["nodes"]["mapped"]["body"]["config"]["agent"], self.agent.sqid)
        self.assertEqual(document["nodes"]["mapped"]["body"]["config"]["agent"], "agents.map_agent")

    def test_provisioned_narrow_agent_advertises_only_explicit_tools_after_catalogue_sync(self):
        with actor_context(self.admin):
            self.agent.with_actor(self.admin).resource_reader = False
            self.agent.save(update_fields=("resource_reader", "updated_at"))
            self.assertTrue(provision_agent(self.agent.sqid).ok)
            sync_builtin_tool_catalogue()
        run, session = self.waiting_run()
        toolset = AngeeToolset(session, ToolGrantAccess(self.agent.principal_subject()), str(self.server.sqid))
        tools = async_to_sync(toolset.get_tools)(SimpleNamespace(max_retries=0))
        self.assertEqual(set(tools), READ_TOOLS)

    def test_resource_import_revokes_a_provisioned_agents_reader_bundle_on_write(self):
        addon = apps.get_app_config("agents")
        ledger = apps.get_model("resources", "Resource")
        resource = build_resource(
            Agent,
            ResourceEntry(addon=addon, tier=ResourceTier.INSTALL, source_value="reader-policy"),
            ledger_model=ledger,
            addon_aliases={"agents": addon.name, addon.name: addon.name},
        )
        rows = tablib.Dataset(headers=["_xref", "resource_reader", "mcp_tools"])
        rows.append(["narrow_assistant", False, [f"agents.tool_{name}" for name in sorted(READ_TOOLS)]])
        with system_context(reason="agent reader import policy"):
            ledger.objects.bind_instance(
                addon=addon,
                xref="narrow_assistant",
                instance=self.agent,
                source="reader-policy",
            )
            self.assertTrue(provision_agent(self.agent.sqid).ok)
            self.assertIn(RESOURCE_READER_ROLE, set(containers_of(self.agent.user)))
            resource.import_data(rows, dry_run=False, raise_errors=True, use_transactions=True, actor=self.admin)
            self.agent.refresh_from_db()
            self.assertFalse(self.agent.resource_reader)
            self.assertNotIn(RESOURCE_READER_ROLE, set(containers_of(self.agent.user)))

    def test_agent_resource_resolves_named_read_tools_without_demo(self):
        addon = apps.get_app_config("agents")
        ledger = apps.get_model("resources", "Resource")
        aliases = {"agents": addon.name, addon.name: addon.name}
        resource = build_resource(
            Agent,
            ResourceEntry(addon=addon, tier=ResourceTier.INSTALL, source_value="installed-agents"),
            ledger_model=ledger,
            addon_aliases=aliases,
        )
        rows = tablib.Dataset(headers=["_xref", "name", "owner", "runtime_class", "mcp_tools"])
        rows.append(
            [
                "installed_assistant",
                "Installed assistant",
                "agents.reply_owner",
                "pydantic",
                [f"agents.tool_{name}" for name in sorted(READ_TOOLS)],
            ]
        )
        with system_context(reason="installed agent resource"):
            ledger.objects.bind_instance(addon=addon, xref="reply_owner", instance=self.owner, source="participants")
            result = resource.import_data(
                rows,
                dry_run=False,
                raise_errors=True,
                use_transactions=True,
                actor=self.admin,
            )
            self.assertFalse(result.has_errors())
            installed = resolve_ledger_xref("agents.installed_assistant")
            self.assertEqual(set(installed.mcp_tools.values_list("name", flat=True)), READ_TOOLS)
            self.assertEqual(resolve_ledger_xref("agents.mcp_angee").pk, self.server.pk)
