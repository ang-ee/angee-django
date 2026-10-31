"""Real producer rollback and delivery boundaries for routed agent conversations."""

from contextvars import Context
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rebac import actor_context, database_context, system_context
from rebac.roles import grant as grant_role

from angee.base.testing.packages import write_addon_distribution
from angee.provenance.context import changeset
from angee.workflows.querysets import StepRunQuerySet
from angee.workflows.testing.drivers import register_steps, run_until
from tests.composed_host import run_composed_tests
from tests.workflow_steps import Echo, document


class ProducerRouter:
    """Exercise native static model placement away from the default connection."""

    def db_for_read(self, model, **hints):
        return "producer"

    db_for_write = db_for_read

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == "producer"


class RoutedSessionContract(TransactionTestCase):
    __test__ = False
    databases = {"default", "producer", "producer_mirror"}

    def test_post_rollback_and_default_commit_cannot_publish_producer_work(self):
        call_command("rebac", "sync", database="producer", verbosity=0)
        Agent = apps.get_model("agents", "Agent")
        Session = apps.get_model("agents", "AgentSession")
        Turn = apps.get_model("agents", "AgentTurn")
        with (
            transaction.atomic(using="producer"),
            database_context("producer"),
            system_context(reason="tests.routed_session.seed"),
            changeset(fresh=True, using="producer"),
        ):
            owner = get_user_model().objects.create_user(username="routed-conversation")
            agent = Agent.objects.create(
                name="Routed conversation",
                owner=owner,
                runtime_class="pydantic",
                lifecycle="ready",
                runtime_status="running",
            )
        with actor_context(owner), changeset(fresh=True, using="producer"):
            session = Session.objects.start(agent, user=owner, context={})
        self.assertEqual(session._state.db, "producer")
        sent = []
        deadline = timezone.now() + timedelta(minutes=1)
        with patch(
            "angee.jobs.enqueue.celery_app.send_task", side_effect=lambda name, **options: sent.append((name, options))
        ):
            with self.assertRaisesRegex(ValueError, "rollback"):
                with (
                    transaction.atomic(using="producer"),
                    actor_context(owner),
                    changeset(fresh=True, using="producer"),
                ):
                    with transaction.atomic(using="default"):
                        session.post("Rolled back", dispatch_expires=deadline)
                    self.assertEqual(sent, [])
                    raise ValueError("rollback")
            self.assertEqual(sent, [])
            self.assertFalse(Turn._base_manager.using("producer").exists())
            with transaction.atomic(using="producer"), actor_context(owner), changeset(fresh=True, using="producer"):
                with transaction.atomic(using="default"):
                    turn = session.post("Committed", dispatch_expires=deadline)
                self.assertEqual(sent, [])
                self.assertEqual(turn._state.db, "producer")
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][0], "agents.run_session")
            self.assertEqual(sent[0][1]["kwargs"], {"session_id": session.pk})
            self.assertEqual(sent[0][1]["expires"], deadline)
            with transaction.atomic(using="producer"), actor_context(owner), changeset(fresh=True, using="producer"):
                session.cancel_active_turn()
                session.close()
            self.assertEqual(len(sent), 1)
        self.assertEqual(Turn._base_manager.using("producer").get().status, "canceled")

    def test_run_admission_and_settlement_use_producer(self):
        call_command("rebac", "sync", database="producer", verbosity=0)
        Workflow = apps.get_model("workflows", "Workflow")
        Run = apps.get_model("workflows", "WorkflowRun")
        StepRun = apps.get_model("workflows", "StepRun")
        with (
            database_context("producer"),
            system_context(reason="tests.routed_workflow.seed"),
            changeset(fresh=True, using="producer"),
            register_steps(Echo),
            patch("angee.jobs.enqueue.celery_app.send_task") as send,
        ):
            owner = get_user_model().objects.create_user(username="routed-workflow")
            grant_role(actor=owner, role="angee/role:admin")
            workflow = Workflow.objects.install_definition(
                key="routed",
                name="Routed",
                draft=document("entry"),
                actor=owner,
            )
            self.assertEqual(workflow._state.db, "producer")
            send.reset_mock()
            with self.assertRaisesRegex(ValueError, "rollback"):
                with transaction.atomic(using="producer"):
                    with transaction.atomic(using="default"):
                        Run.objects.start(workflow, actor=owner)
                    self.assertFalse(send.called)
                    raise ValueError("rollback")
            self.assertFalse(Run._base_manager.using("producer").exists())
            self.assertFalse(send.called)
            run = Run.objects.start(workflow, actor=owner)
            self.assertEqual(run._state.db, "producer")
            self.assertTrue(send.called)
            Context().run(run_until, run)
            run.refresh_from_db(using="producer")
            self.assertEqual(
                run.status,
                "succeeded",
                (run.error, list(StepRun._base_manager.using("producer").filter(run=run).values())),
            )
            self.assertEqual(StepRun._base_manager.using("producer").get(run=run).status, "succeeded")

    def test_nested_alias_holds_publish_at_their_own_commit(self):
        call_command("rebac", "sync", database="producer", verbosity=0)
        Workflow = apps.get_model("workflows", "Workflow")
        Run = apps.get_model("workflows", "WorkflowRun")
        with (
            database_context("producer"),
            system_context(reason="tests.routed_workflow.hold_seed"),
            changeset(fresh=True, using="producer"),
            register_steps(Echo),
            patch("angee.jobs.enqueue.celery_app.send_task"),
        ):
            owner = get_user_model().objects.create_user(username="routed-holds")
            grant_role(actor=owner, role="angee/role:admin")
            workflow = Workflow.objects.install_definition(
                key="routed-holds",
                name="Routed holds",
                draft=document("entry"),
                actor=owner,
            )
            run = Run.objects.start(workflow, actor=owner)
        dispatched = []
        with patch.object(
            StepRunQuerySet, "dispatch", autospec=True, side_effect=lambda rows: dispatched.append(rows.db)
        ):
            with Run.objects.db_manager("producer").hold(run.pk):
                with Run.objects.db_manager("producer_mirror").hold(run.pk):
                    self.assertEqual(dispatched, [])
                self.assertEqual(dispatched, ["producer_mirror"])
            self.assertEqual(dispatched, ["producer_mirror", "producer"])


def test_routed_conversation_producer_contract(tmp_path: Path):
    addon = tmp_path / "addons" / "example" / "routed_conversations"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(
        addon, "example.routed_conversations", depends_on=("angee.agents_runtime_pydantic", "angee.workflows")
    )
    (addon / "autoconfig.py").write_text(
        'SETTINGS = {"DATABASE_ROUTERS": ["tests.test_workflows_refactor_routing.ProducerRouter"], '
        '"DATABASES.producer": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}, '
        '"DATABASES.producer_mirror": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:", '
        '"TEST": {"MIRROR": "producer"}}}\n'
    )
    run_composed_tests(
        tmp_path,
        "tests.test_workflows_refactor_routing.RoutedSessionContract",
        app="example.routed_conversations",
        addon_dirs=(tmp_path / "addons",),
    )
