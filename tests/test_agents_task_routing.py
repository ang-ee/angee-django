"""Real queued inference delivery preserves its session's database identity."""

from contextlib import asynccontextmanager
from contextvars import Context
from pathlib import Path
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from pydantic_ai.models.test import TestModel
from rebac import actor_context, current_actor, database_context, system_context
from rebac.database import read_alias

from angee.agents.backends import InferenceBackend
from angee.agents.runners import TurnOutcome
from angee.agents.tasks import run_session
from angee.base.testing.packages import write_addon_distribution
from angee.provenance.context import changeset
from tests.composed_host import run_composed_tests


class RoutingInferenceBackend(InferenceBackend):
    """Use the SDK's own deterministic model through the real provider contract."""

    key = "routing_test"
    calls = []

    @asynccontextmanager
    async def model(self, handle, *, credential=None):
        assert handle == "routing-model"
        assert credential is None
        self.calls.append((self.provider._state.db, current_actor(), read_alias(type(self.provider))))
        yield TestModel(call_tools=[], custom_output_text=f"Reply from {self.provider.name}")


class SessionWriteRouter:
    """Legacy tasks still honor native model placement without a payload alias."""

    def db_for_write(self, model, **hints):
        return "producer" if model._meta.label_lower == "agents.agentsession" else None


class AgentTaskRoutingContract(TransactionTestCase):
    __test__ = False
    databases = {"default", "producer"}
    reset_sequences = True
    maxDiff = None

    def setUp(self):
        self.Session = apps.get_model("agents", "AgentSession")
        self.Turn = apps.get_model("agents", "AgentTurn")
        self.Agent = apps.get_model("agents", "Agent")
        RoutingInferenceBackend.calls.clear()
        call_command("rebac", "sync", database="default", verbosity=0)
        with (
            database_context("default"),
            system_context(reason="tests.agents.routing.seed"),
            changeset(fresh=True, using="default"),
        ):
            self.owner = get_user_model().objects.create_user(username="routed-inference-owner")
            vendor, _ = apps.get_model("integrate", "Vendor").objects.get_or_create(slug="routing")
            provider = apps.get_model("agents", "InferenceProvider").objects.create(
                owner=self.owner, vendor=vendor, name="default", backend_class="routing_test",
            )
            model = apps.get_model("agents", "InferenceModel").objects.create(
                provider=provider, name="routing-model",
            )
            self.agent = self.Agent.objects.create(
                name="Routed inference", owner=self.owner, model=model, runtime_class="pydantic",
                lifecycle="ready", runtime_status="running",
            )
            apps.get_model("agents", "MCPServer").objects.builtin(create=True)
        with actor_context(self.owner), changeset(fresh=True, using="default"):
            self.session = self.Session.objects.start(self.agent, user=self.owner, context={})
            with patch("angee.jobs.enqueue.celery_app.send_task"):
                self.first = self.session.post("First")
                self.second = self.session.post("Second")
        # Preserve all identities, including principal grants, in both real ledgers.
        connections["producer"].ensure_connection()
        connections["default"].connection.backup(connections["producer"].connection)
        with database_context("producer"), system_context(reason="tests.agents.routing.distinguish"):
            apps.get_model("agents", "InferenceProvider").objects.using("producer").filter(pk=provider.pk).update(
                name="producer",
            )
        self.selected = self.Session._base_manager.using("producer").get(pk=self.session.pk)
        self.selected_owner = get_user_model()._base_manager.using("producer").get(pk=self.owner.pk)

    def _snapshot(self, using):
        return {
            model._meta.label: list(model._base_manager.using(using).order_by("pk").values())
            for model in (self.Session, self.Turn)
        }

    def _deliver(self, payload):
        def native_delivery():
            with changeset(fresh=True, using=payload.get("using")):
                return run_session.apply(kwargs=payload, throw=True).get()
        return Context().run(native_delivery)

    def test_real_delivery_continuation_and_legacy_payload_keep_their_ledger(self):
        baseline = self._snapshot("default")
        sent = []
        with patch("angee.jobs.enqueue.celery_app.send_task", side_effect=lambda name, **kw: sent.append((name, kw))):
            with transaction.atomic(using="producer"):
                self.selected.enqueue_execution()
                self.assertEqual(sent, [])
            name, options = sent.pop()
            self.assertEqual(name, "agents.run_session")
            self.assertEqual(options["kwargs"], {"session_id": self.session.pk, "using": "producer"})
            with CaptureQueriesContext(connections["default"]) as default_queries:
                self._deliver(options["kwargs"])
            self.assertEqual(list(default_queries), [])
            first = self.Turn._base_manager.using("producer").get(pk=self.first.pk)
            self.assertEqual((first.status, first.text), ("completed", "Reply from producer"), first.error)
            self.assertEqual(self._snapshot("default"), baseline)
            self.assertEqual(len(sent), 1)
            continuation = sent.pop()[1]["kwargs"]
            self.assertEqual(continuation, {"session_id": self.session.pk, "using": "producer"})
            self._deliver(continuation)
            second = self.Turn._base_manager.using("producer").get(pk=self.second.pk)
            self.assertEqual((second.status, second.text), ("completed", "Reply from producer"), second.error)
            self.assertEqual(sent, [])
            completed = self._snapshot("producer")
            self._deliver(continuation)  # Duplicate delivery cannot reclaim settled work.
            self.assertEqual(self._snapshot("producer"), completed)
            self.assertEqual(self._snapshot("default"), baseline)
            self.assertEqual([call[0] for call in RoutingInferenceBackend.calls], ["producer", "producer"])
            principal = self.Agent._base_manager.using("producer").get(pk=self.agent.pk).principal_subject()
            self.assertTrue(all(call[1:] == (principal, "producer") for call in RoutingInferenceBackend.calls))
            # Existing messages without using honor a configured native write router.
            with override_settings(DATABASE_ROUTERS=[SessionWriteRouter()]):
                self._deliver({"session_id": self.session.pk})
            self.assertEqual(self._snapshot("default"), baseline)
            # Without such placement, legacy delivery still selects default.
            with CaptureQueriesContext(connections["producer"]) as producer_queries:
                self._deliver({"session_id": self.session.pk})
            self.assertEqual(list(producer_queries), [])
            self.assertEqual(self.Turn._base_manager.using("default").get(pk=self.first.pk).status, "completed")
            self.assertEqual(self.Turn._base_manager.using("default").get(pk=self.second.pk).status, "pending")

    def test_cancel_continuation_rollback_and_selected_atomic_refusal(self):
        baseline = self._snapshot("default")
        sent = []
        with patch("angee.jobs.enqueue.celery_app.send_task", side_effect=lambda name, **kw: sent.append((name, kw))):
            with self.assertRaisesRegex(ValueError, "rollback"):
                with transaction.atomic(using="producer"):
                    self.selected.enqueue_execution()
                    raise ValueError("rollback")
            self.assertEqual(sent, [])
            with transaction.atomic(using="producer"):
                with self.assertRaisesRegex(RuntimeError, "outside a database transaction"):
                    self._deliver({"session_id": self.selected.pk, "using": "producer"})
            with database_context("producer"), changeset(fresh=True, using="producer"):
                claimed = self.selected.claim_turn()
            self.assertEqual(claimed.pk, self.first.pk)
            self._deliver({"session_id": self.selected.pk, "using": "producer"})
            self.assertEqual(RoutingInferenceBackend.calls, [])  # Active claims cannot be stolen.
            with actor_context(self.selected_owner), changeset(fresh=True, using="producer"):
                self.selected.cancel_turn(claimed)
                self.selected.settle_turn(claimed, TurnOutcome(kind="completed", text="Stale worker"))
            self.assertEqual(sent[-1][1]["kwargs"], {"session_id": self.selected.pk, "using": "producer"})
            with CaptureQueriesContext(connections["default"]) as queries:
                self._deliver(sent.pop()[1]["kwargs"])
            self.assertEqual(list(queries), [])
            claimed.refresh_from_db(using="producer")
            self.assertEqual(claimed.status, "canceled")
            second = self.Turn._base_manager.using("producer").get(pk=self.second.pk)
            self.assertEqual(second.status, "completed", second.error)
            self.assertEqual(self._snapshot("default"), baseline)


def test_native_agent_task_routing(tmp_path: Path):
    addon = tmp_path / "addons" / "example" / "agent_task_routing"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(addon, "example.agent_task_routing", depends_on=("angee.agents_runtime_pydantic",))
    (addon / "autoconfig.py").write_text(
        'SETTINGS = {"DATABASES.producer": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}, '
        '"ANGEE_INFERENCE_BACKEND_CLASSES.routing_test": '
        '"tests.test_agents_task_routing.RoutingInferenceBackend"}\n'
    )
    run_composed_tests(
        tmp_path, "tests.test_agents_task_routing.AgentTaskRoutingContract",
        app="example.agent_task_routing", addon_dirs=(tmp_path / "addons",),
    )
