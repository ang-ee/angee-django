"""Real changeset-close and record-deletion subscribers stay on the producer."""

from pathlib import Path
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, database_context, system_context
from rebac.models import PermissionAuditEvent
from rebac.roles import grant as grant_role

from angee.base.models.identity import public_id_of
from angee.base.signals import change_published
from angee.base.testing.packages import write_addon_distribution
from angee.provenance.context import changeset, current_changeset
from angee.provenance.timeline import ChangeKind
from angee.timeline.reading import Timeline
from angee.workflows.testing.drivers import register_steps
from tests.composed_host import run_composed_tests
from tests.workflow_steps import Echo, document


class CallbackRoutingContract(TransactionTestCase):
    __test__ = False
    databases = {"default", "producer"}
    reset_sequences = True

    def setUp(self):
        self.Record = apps.get_model("callback_routing", "Record")
        self.users = {}
        self.records = {}
        for alias, pk in (("default", 11), ("producer", 22)):
            call_command("rebac", "sync", database=alias, verbosity=0)
            with (
                database_context(alias),
                system_context(reason="tests.callback.seed"),
                changeset(fresh=True, using=alias),
            ):
                user = get_user_model().objects.db_manager(alias).create_user(username="callback-owner")
                self.users[alias] = user
                self.records[alias] = self.Record.objects.db_manager(alias).create(pk=pk, owner=user, label=alias)

    def test_actual_close_subscribers_read_and_audit_only_producer(self):
        Changeset = apps.get_model("provenance", "Changeset")
        with database_context("default"), actor_context(self.users["default"]), changeset(fresh=True, using="default"):
            self.records["default"].label = "default unchanged by producer"
            self.records["default"].save(using="default", update_fields=["label"])
            default_receipt = current_changeset(required=True)
        observed = []

        def closed(sender, *, payload, **kwargs):
            observed.append((payload.instance._state.db, payload.instance.state))

        change_published.connect(closed, sender=Changeset, weak=False)
        self.addCleanup(change_published.disconnect, closed, sender=Changeset)
        with (
            CaptureQueriesContext(connections["default"]) as default_queries,
            CaptureQueriesContext(connections["producer"]) as producer_queries,
            database_context("default"),
            actor_context(self.users["producer"]),
        ):
            with changeset(fresh=True, using="producer"):
                with database_context("producer"):
                    self.records["producer"].label = "producer committed"
                    self.records["producer"].save(using="producer", update_fields=["label"])
                    receipt = current_changeset(required=True)
            self.assertEqual(observed, [("producer", "closed")])
        self.assertEqual(default_receipt.pk, receipt.pk)
        self.assertEqual(list(default_queries), [])
        self.assertTrue(any('"touched"' in query["sql"] for query in producer_queries))
        with database_context("producer"), system_context(reason="tests.callback.inspect"):
            self.assertEqual(set(ChangeKind().records(receipt)), {(self.Record, 22)})
            self.assertTrue(
                PermissionAuditEvent.objects.using("producer").filter(reason="timeline.invalidate").exists()
            )
        with database_context("default"), system_context(reason="tests.callback.inspect"):
            self.records["default"].refresh_from_db(using="default")
            self.assertEqual(self.records["default"].label, "default unchanged by producer")

    def _run(self, alias):
        Workflow = apps.get_model("workflows", "Workflow")
        Run = apps.get_model("workflows", "WorkflowRun")
        with (
            database_context(alias),
            system_context(reason="tests.callback.workflow"),
            changeset(fresh=True, using=alias),
            register_steps(Echo),
        ):
            grant_role(actor=self.users[alias], role="angee/role:admin")
            workflow = Workflow.objects.db_manager(alias).install_definition(
                key="callback",
                name="Callback",
                draft=document("entry"),
                actor=self.users[alias],
                subject_model=self.Record._meta.label,
            )
            return Run.objects.db_manager(alias).start(workflow, actor=self.users[alias], subject=self.records[alias])

    def test_run_hydration_keeps_the_producer_when_the_ambient_database_changes(self):
        with patch("angee.jobs.enqueue.celery_app.send_task"):
            default_run = self._run("default")
            run = self._run("producer")
        self.assertEqual(default_run.pk, run.pk)
        with self.settings(ANGEE_TIMELINE_KINDS=["angee.workflows.timeline.timeline_kinds"]):
            with database_context("producer"), actor_context(self.users["producer"]):
                timeline = Timeline(self.records["producer"], self.users["producer"])
            with (
                CaptureQueriesContext(connections["default"]) as default_queries,
                database_context("default"),
                actor_context(self.users["producer"]),
            ):
                page = timeline.page()
        self.assertEqual(list(default_queries), [])
        self.assertEqual([entry.target_id for entry in page.entries], [public_id_of(run)])
        self.assertEqual(page.entries[0].title, "Callback")
        self.assertEqual(page.entries[0].actor, self.users["producer"].get_username())

    def test_actual_producer_deletion_retains_cancel_actor_and_commit_boundary(self):
        with patch("angee.jobs.enqueue.celery_app.send_task") as send:
            default_run = self._run("default")
            run = self._run("producer")
            self.assertEqual(default_run.pk, run.pk)
            send.reset_mock()
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with (
                    CaptureQueriesContext(connections["default"]) as rollback_queries,
                    database_context("default"),
                    actor_context(self.users["producer"]),
                    changeset(fresh=True, using="producer"),
                    transaction.atomic(using="producer"),
                ):
                    self.Record.objects.db_manager("producer").with_actor(self.users["producer"]).filter(pk=22).delete()
                    self.assertFalse(send.called)
                    raise RuntimeError("rollback")
            self.assertEqual(list(rollback_queries), [])
            self.assertFalse(send.called)
            self.assertTrue(self.Record._base_manager.using("producer").filter(pk=22).exists())
            with (
                CaptureQueriesContext(connections["default"]) as default_queries,
                database_context("default"),
                actor_context(self.users["producer"]),
                changeset(fresh=True, using="producer"),
            ):
                with transaction.atomic(using="producer"):
                    self.Record.objects.db_manager("producer").with_actor(self.users["producer"]).filter(pk=22).delete()
                    self.assertFalse(send.called)
                self.assertTrue(send.called)
            self.assertEqual(list(default_queries), [])
            cancellations = [call for call in send.call_args_list if call.args[0] == "workflows.cancel"]
            self.assertEqual(len(cancellations), 1)
            self.assertEqual(
                cancellations[0].kwargs["kwargs"],
                {"run_id": run.pk, "actor": f"auth/user:{self.users['producer'].pk}", "using": "producer"},
            )
        default_run.refresh_from_db(using="default")
        self.assertFalse(default_run.is_terminal)
        self.assertTrue(self.Record._base_manager.using("default").filter(pk=11).exists())
        self.assertFalse(self.Record._base_manager.using("producer").filter(pk=22).exists())


def write_callback_addon(tmp_path: Path, *, depends_on=("angee.workflows",)):
    addon = tmp_path / "addons" / "example" / "callback_routing"
    addon.mkdir(parents=True)
    (addon / "__init__.py").touch()
    write_addon_distribution(addon, "example.callback_routing", depends_on=depends_on)
    (addon / "models.py").write_text(
        "from django.db import models\n"
        "from angee.base.models.base import AngeeDataModel\n"
        "from angee.base.models.ownership import OwnerMixin\n"
        "from angee.provenance.contrib.angee.history import HistoryMixin\n"
        "class Record(HistoryMixin, OwnerMixin, AngeeDataModel, concrete=True):\n"
        " label = models.CharField(max_length=100)\n"
        " class Meta:\n  abstract = True\n  rebac_resource_type = 'callback/record'\n"
    )
    (addon / "permissions.zed").write_text(
        "// revision: 1\ndefinition callback/record {\n"
        " relation owner: auth/user // rebac:field=owner\n"
        " permission create = owner\n permission read = owner\n permission write = owner\n"
        " permission delete = owner\n permission transfer = owner\n permission write__owner = transfer\n}\n"
    )
    (addon / "autoconfig.py").write_text(
        'SETTINGS = {"DATABASES.producer": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}\n'
    )
    return addon


def test_native_producer_callback_routing(tmp_path: Path):
    write_callback_addon(tmp_path)
    run_composed_tests(
        tmp_path,
        "tests.test_timeline_routing.CallbackRoutingContract",
        app="example.callback_routing",
        addon_dirs=(tmp_path / "addons",),
    )
