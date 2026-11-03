"""Captured native deliveries keep producer identities through cleanup and wake."""

from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connections, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, database_context, system_context, to_subject_ref
from rebac.roles import grant as grant_role

from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.provenance.context import changeset
from angee.workflows import tasks
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.signals import isolated_run_cleanup, run_stopping
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import capture_tasks, deliver_decision_wakes, register_steps
from tests.composed_host import run_composed_tests
from tests.test_timeline_routing import CallbackRoutingContract, write_callback_addon
from tests.workflow_steps import Echo


class RecordEcho(Echo):
    key = "delivery_record"

    def run(self, ctx):
        record = ctx.subject
        record.label = "executed on producer"
        record.save(using=record._state.db, update_fields=["label"])
        ctx.record(record, operation="changed")
        return ctx.done(ctx.input)


class RejectRecord(RecordEcho):
    key = "delivery_reject"

    def run(self, ctx):
        super().run(ctx)
        return ctx.fail("Rejected by producer body.")


class Finish(Step[None, None, None]):
    key = "delivery_finish"

    def run(self, ctx):
        return ctx.done(ctx.input)


class RoutingReview(DecisionStep[None, None, None]):
    key = "delivery_review"
    outcomes = {"approved": "Approved"}

    def ask(self, ctx):
        return ctx.ask(
            DecisionRequest(
                kind="delivery_review",
                records=(ctx.subject,),
                assignees=(ctx.actor,),
                proposal=DecisionProposal(
                    alternatives=[
                        {"key": "approve", "label": "Approve", "outcome": "approved"},
                    ]
                ),
            )
        )


class WorkflowWriteRouter:
    def db_for_write(self, model, **hints):
        return "producer" if model._meta.app_label == "workflows" else None


class DeliveryRoutingContract(CallbackRoutingContract):
    maxDiff = None

    def _payload(self, send, name, **identity):
        values = [
            call.kwargs["kwargs"]
            for call in send.call_args_list
            if call.args[0] == name and all(call.kwargs["kwargs"].get(key) == value for key, value in identity.items())
        ]
        self.assertTrue(values, (name, identity, send.call_args_list))
        return values[-1]

    def test_answered_decision_wake_uses_the_producer_commit_and_database(self):
        alias = "producer"
        Run = apps.get_model("workflows", "WorkflowRun")
        StepRun = apps.get_model("workflows", "StepRun")
        Decision = apps.get_model("decisions", "Decision")
        actor = self.users[alias]
        draft = {
            "nodes": {"review": {"step": RoutingReview.key}},
            "results": [{"from": "review", "when": ["approved"], "as": "approved"}],
        }

        with capture_tasks() as sent, register_steps(RoutingReview):
            workflow = self._workflow(alias, "decision-routing", draft, subject_model=self.Record._meta.label)
            with database_context(alias), actor_context(actor), changeset(fresh=True, using=alias):
                run = Run.objects.db_manager(alias).start(workflow, actor=actor, subject=self.records[alias])
            step = self._step(run, node_key="review")
            execute = next(
                payload["kwargs"]
                for name, payload in sent
                if name == "workflows.execute" and payload["kwargs"]["step_run_id"] == step.pk
            )
            self.assertTrue(tasks.execute.apply(kwargs=execute, throw=True).get())
            step.refresh_from_db(using=alias)
            self.assertEqual((step.status, step.waiting_kind), ("waiting", "decision"))
            decision = Decision.system_queryset().using(alias).get(pk=step.decision_id)
            before = len(sent)

            with database_context("default"), transaction.atomic(using="default"):
                with changeset(fresh=True, using=alias), transaction.atomic(using=alias):
                    decision.decide(actor=actor, chosen=["approve"], revision=decision.revision)
                    self.assertEqual(sent[before:], [])
                wakes = [payload for name, payload in sent[before:] if name == "workflows.wake_decisions"]
                self.assertEqual(len(wakes), 1)
                self.assertEqual(wakes[0]["kwargs"], {"decision_id": decision.pk, "using": alias})

            with CaptureQueriesContext(connections["default"]) as default_queries, database_context("default"):
                self.assertEqual(deliver_decision_wakes(sent[before:], decision.pk), [1])
            self.assertEqual(list(default_queries), [])
            self.assertEqual(StepRun.system_queryset().using(alias).get(pk=step.pk).status, "ready")

    def test_actual_cancel_delivery_and_legacy_router_recheck_permissions(self):
        with patch("angee.jobs.enqueue.celery_app.send_task") as send:
            default = self._run("default")
            run = self._run("producer")
            with (
                database_context("producer"),
                system_context(reason="tests.delivery.outsider"),
                changeset(fresh=True, using="producer"),
            ):
                outsider = get_user_model().objects.db_manager("producer").create_user(username="not-run-owner")
            with CaptureQueriesContext(connections["default"]) as default_queries, database_context("default"):
                with self.assertRaises(PermissionDenied):
                    tasks.cancel.apply(
                        kwargs={"run_id": run.pk, "actor": str(to_subject_ref(outsider)), "using": "producer"},
                        throw=True,
                    )
            self.assertEqual(list(default_queries), [])
            run.refresh_from_db(using="producer")
            self.assertFalse(run.is_terminal)
            send.reset_mock()
            with (
                database_context("default"),
                actor_context(self.users["producer"]),
                transaction.atomic(using="producer"),
            ):
                run.cancel_on_commit(self.users["producer"])
                self.assertFalse(send.called)
            payload = self._payload(send, "workflows.cancel")
            self.assertEqual(payload["using"], "producer")
            with CaptureQueriesContext(connections["default"]) as default_queries, database_context("default"):
                tasks.cancel.apply(kwargs=payload, throw=True)
            self.assertEqual(list(default_queries), [])
            run.refresh_from_db(using="producer")
            self.assertEqual(run.status, "canceled")
            completed_at = run.finished_at
            tasks.cancel.apply(kwargs=payload, throw=True)
            run.refresh_from_db(using="producer")
            self.assertEqual(run.finished_at, completed_at)
            default.refresh_from_db(using="default")
            self.assertFalse(default.is_terminal)
            # Existing queued calls without the new optional argument remain usable.
            tasks.cancel.apply(
                kwargs={"run_id": default.pk, "actor": str(to_subject_ref(self.users["default"]))}, throw=True
            )
            default.refresh_from_db(using="default")
            self.assertEqual(default.status, "canceled")

    def test_legacy_payload_uses_native_write_router_on_colliding_ids(self):
        with patch("angee.jobs.enqueue.celery_app.send_task"):
            default = self._run("default")
            run = self._run("producer")
            with (
                override_settings(DATABASE_ROUTERS=[WorkflowWriteRouter()]),
                CaptureQueriesContext(connections["default"]) as default_queries,
                database_context("default"),
            ):
                tasks.cancel.apply(
                    kwargs={"run_id": run.pk, "actor": str(to_subject_ref(self.users["producer"]))}, throw=True
                )
            self.assertEqual(list(default_queries), [])
        run.refresh_from_db(using="producer")
        default.refresh_from_db(using="default")
        self.assertEqual(run.status, "canceled")
        self.assertFalse(default.is_terminal)

    def _step(self, run, **filters):
        with database_context(run._state.db):
            return run.step_runs.with_actor(self.users[run._state.db]).get(**filters)

    def _sessions(self, alias, run):
        Agent = apps.get_model("agents", "Agent")
        Session = apps.get_model("agents", "AgentSession")
        StepRecord = apps.get_model("workflows", "StepRecord")
        ContentType = apps.get_model("contenttypes", "ContentType")
        with (
            database_context(alias),
            system_context(reason="tests.delivery.sessions"),
            changeset(fresh=True, using=alias),
        ):
            agent = Agent.objects.db_manager(alias).create(
                name="Cleanup",
                owner=self.users[alias],
                runtime_class="pydantic",
                lifecycle="ready",
                runtime_status="running",
            )
            owned = Session.objects.db_manager(alias).start(
                agent, user=self.users[alias], context={}, actor=self.users[alias]
            )
            owned_turn = owned.post("owned", actor=self.users[alias])
            continued = Session.objects.db_manager(alias).start(
                agent, user=self.users[alias], context={}, actor=self.users[alias]
            )
            turn = continued.post("created turn", actor=self.users[alias])
            step = self._step(run)
            for record in (owned, turn):
                StepRecord.objects.db_manager(alias).create(
                    run=run,
                    step_run=step,
                    content_type=ContentType.objects.db_manager(alias).get_for_model(record),
                    object_id=record.pk,
                    operation="created",
                )
            return owned, owned_turn, continued, turn

    def test_real_intersection_cleanup_and_failed_receiver_use_producer_savepoints(self):
        with patch("angee.jobs.enqueue.celery_app.send_task") as send:
            default_run = self._run("default")
            run = self._run("producer")
            default = self._sessions("default", default_run)
            selected = self._sessions("producer", run)
            self.assertEqual([row.pk for row in default], [row.pk for row in selected])
            observed = []

            @isolated_run_cleanup
            def failed(sender, *, run, **kwargs):
                self.Record.system_queryset().using(run._state.db).filter(pk=22).update(label="rolled-back cleanup")
                raise ValueError("cleanup refused")

            @isolated_run_cleanup
            def following(sender, *, run, **kwargs):
                observed.append((run._state.db, self.Record.system_queryset().using(run._state.db).get(pk=22).label))

            run_stopping.connect(failed, weak=False)
            run_stopping.connect(following, weak=False)
            self.addCleanup(run_stopping.disconnect, failed)
            self.addCleanup(run_stopping.disconnect, following)
            with database_context("default"), actor_context(self.users["producer"]):
                run.cancel_on_commit(self.users["producer"])
            payload = self._payload(send, "workflows.cancel")
            with (
                self.assertLogs("angee.workflows.signals", level="ERROR"),
                CaptureQueriesContext(connections["default"]) as default_queries,
            ):
                tasks.cancel.apply(kwargs=payload, throw=True)
            self.assertEqual(list(default_queries), [])
            self.assertEqual(observed, [("producer", "producer")])
        for row in (*default, *selected):
            row.refresh_from_db(using=row._state.db)
        self.assertEqual(selected[0].status, "closed")
        self.assertEqual(selected[1].status, "canceled")
        self.assertEqual(selected[2].status, "idle")
        self.assertEqual(selected[3].status, "canceled")
        self.assertNotEqual(default[0].status, "closed")
        self.assertNotEqual(default[1].status, "canceled")
        self.assertNotEqual(default[3].status, "canceled")

    def _workflow(self, alias, key, draft, *, subject_model=""):
        Workflow = apps.get_model("workflows", "Workflow")
        with (
            database_context(alias),
            system_context(reason="tests.delivery.definition"),
            changeset(fresh=True, using=alias),
        ):
            grant_role(actor=self.users[alias], role="angee/role:admin")
            return Workflow.objects.db_manager(alias).install_definition(
                key=key, name=key, draft=draft, actor=self.users[alias], subject_model=subject_model
            )

    def test_cancel_wake_dispatch_execute_continuation_reaches_terminal_on_producer(self):
        Run = apps.get_model("workflows", "WorkflowRun")
        parent_draft = {
            "nodes": {
                "await": {
                    "step": "await_run",
                    "config": {"expects": "callback"},
                    "next": {outcome: "finish" for outcome in ("done", "error", "canceled")},
                },
                "finish": {"step": Finish.key},
            }
        }
        with patch("angee.jobs.enqueue.celery_app.send_task") as send, register_steps(Echo, Finish):
            children, parents = {}, {}
            for alias in ("default", "producer"):
                children[alias] = self._run(alias)
                definition = self._workflow(alias, "parent", parent_draft)
                with database_context(alias), actor_context(self.users[alias]), changeset(fresh=True, using=alias):
                    parents[alias] = Run.objects.db_manager(alias).start(
                        definition, actor=self.users[alias], input={"run_id": str(children[alias].sqid)}
                    )
            waiter = self._step(parents["producer"], node_key="await")
            initial = self._payload(send, "workflows.execute", step_run_id=waiter.pk, using="producer")
            tasks.execute.apply(kwargs=initial, throw=True)
            waiter.refresh_from_db(using="producer")
            self.assertEqual(waiter.status, "waiting")
            send.reset_mock()
            with database_context("default"), actor_context(self.users["producer"]):
                children["producer"].cancel_on_commit(self.users["producer"])
            cancel = self._payload(send, "workflows.cancel")
            with CaptureQueriesContext(connections["default"]) as default_queries, database_context("default"):
                tasks.cancel.apply(kwargs=cancel, throw=True)
                wake = self._payload(send, "workflows.wake_run", run_id=children["producer"].pk, using="producer")
                self.assertEqual(tasks.wake_run.apply(kwargs=wake, throw=True).get(), 1)
                execute = self._payload(send, "workflows.execute", step_run_id=waiter.pk, using="producer")
                self.assertTrue(tasks.execute.apply(kwargs=execute, throw=True).get())
                finish = self._step(parents["producer"], node_key="finish")
                execute = self._payload(send, "workflows.execute", step_run_id=finish.pk, using="producer")
                self.assertTrue(tasks.execute.apply(kwargs=execute, throw=True).get())
                terminal = self._payload(send, "workflows.wake_run", run_id=parents["producer"].pk, using="producer")
                self.assertEqual(tasks.wake_run.apply(kwargs=terminal, throw=True).get(), 0)
            self.assertEqual(list(default_queries), [])
        parents["producer"].refresh_from_db(using="producer")
        self.assertEqual(parents["producer"].status, "succeeded")
        parents["default"].refresh_from_db(using="default")
        children["default"].refresh_from_db(using="default")
        self.assertFalse(parents["default"].is_terminal)
        self.assertFalse(children["default"].is_terminal)

    def test_selected_success_and_failed_body_keep_record_admission_and_rollback(self):
        Run = apps.get_model("workflows", "WorkflowRun")
        with patch("angee.jobs.enqueue.celery_app.send_task") as send, register_steps(RecordEcho, RejectRecord):
            for step in (RejectRecord, RecordEcho):
                definition = self._workflow(
                    "producer",
                    step.key,
                    {"nodes": {"entry": {"step": step.key}}},
                    subject_model=self.Record._meta.label,
                )
                with (
                    database_context("producer"),
                    actor_context(self.users["producer"]),
                    changeset(fresh=True, using="producer"),
                ):
                    run = Run.objects.db_manager("producer").start(
                        definition, actor=self.users["producer"], subject=self.records["producer"]
                    )
                payload = self._payload(send, "workflows.execute", step_run_id=self._step(run).pk, using="producer")
                with CaptureQueriesContext(connections["default"]) as default_queries, database_context("default"):
                    self.assertTrue(tasks.execute.apply(kwargs=payload, throw=True).get())
                self.assertEqual(list(default_queries), [])
                run.refresh_from_db(using="producer")
                self.records["producer"].refresh_from_db(using="producer")
                self.assertEqual(run.status, "failed" if step is RejectRecord else "succeeded")
                self.assertEqual(
                    self.records["producer"].label, "producer" if step is RejectRecord else "executed on producer"
                )
        self.records["default"].refresh_from_db(using="default")
        self.assertEqual(self.records["default"].label, "default")


def test_native_workflow_delivery_routing(tmp_path):
    write_callback_addon(tmp_path, depends_on=("angee.workflows_agents", "angee.agents_runtime_pydantic"))
    run_composed_tests(
        tmp_path,
        "tests.test_workflows_delivery_routing.DeliveryRoutingContract",
        app="example.callback_routing",
        addon_dirs=(tmp_path / "addons",),
    )
