"""Real producer transactions, delayed routing and retained generation fences."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rebac import database_context, system_context

from angee.base.locks import record_lock_key, task_lock, task_lock_is_held
from angee.base.testing.packages import write_addon_distribution
from angee.integrate import live as live_module
from angee.integrate.backends.base import BridgeImpl, ConnectionDiscovery
from angee.integrate.states import StreamDefinition, StreamKind, SyncFailureKind
from angee.integrate.streams import ApplyResult, RecordChange, StreamPage, advance_stream
from angee.integrate.tasks import discover_connection, sync_bridge_now
from angee.provenance.context import changeset
from tests.composed_host import run_composed_tests


class RoutingBackend(BridgeImpl):
    registry_setting = "ANGEE_ROUTING_BACKEND_CLASSES"
    key = "routing"
    requires_connection_discovery = True
    sync_parallelism = 1

    def discover_connection(self, credential):
        assert not connections[self.bridge._state.db].in_atomic_block
        return ConnectionDiscovery(data={"ready": True})

    def apply_discovery(self, discovery):
        self.bridge.merge_subscription_state(discovered=discovery.data["ready"])

    def streams(self, *, deadline=None):
        return (StreamDefinition("records", kind=StreamKind.RECORD_REPLICA),)

    def extract(self, stream, page_bound, *, deadline=None):
        assert not connections[stream._state.db].in_atomic_block
        return StreamPage(
            records=(RecordChange("remote:applied", {"value": 7}, "v1", mapped_payload={"value": 7}),),
            cursor={"page": 1},
            exhausted=True,
        )

    def apply_record(self, stream, record):
        target_model = apps.get_model("routed_integrate", "ImportedRecord")
        target, _ = target_model.objects.db_manager(stream._state.db).get_or_create(
            key=record.external_key,
            defaults={"value": record.source_payload["value"]},
        )
        return ApplyResult(target=target, mapped_payload={"value": target.value})


class RoutingContract(TransactionTestCase):
    __test__ = False
    databases = {"default", "producer"}

    def setUp(self):
        for alias in self.databases:
            call_command("rebac", "sync", database=alias, verbosity=0)

    def _bridge(self, using, name):
        Bridge = apps.get_model("routed_integrate", "RoutedBridge")
        Credential = apps.get_model("integrate", "Credential")
        Vendor = apps.get_model("integrate", "Vendor")
        with (
            database_context(using),
            system_context(reason="tests.integrate.routing.seed"),
            changeset(fresh=True, using=using),
            transaction.atomic(using=using),
        ):
            user = get_user_model().objects.db_manager(using).create_user(username=name)
            vendor = Vendor.objects.db_manager(using).create(slug=name, display_name=name)
            credential = Credential.objects.db_manager(using).create_local_credential(
                user,
                name=name,
                kind="static_token",
                material={"token": "test-token"},
            )
            bridge = Bridge.objects.db_manager(using).create(
                owner=user,
                vendor=vendor,
                credential=credential,
                lifecycle="connected",
                display_name=name,
            )
        return bridge

    def _ready(self, using, name):
        sent = []
        with patch(
            "angee.jobs.enqueue.celery_app.send_task", side_effect=lambda task, **options: sent.append((task, options))
        ):
            bridge = self._bridge(using, name)
        delivery = next(options["kwargs"] for task, options in sent if task == "integrate.discover_connection")
        self.assertEqual(delivery["using"], using)
        self.assertTrue(discover_connection(**delivery)["ok"])
        bridge.refresh_from_db(using=using)
        self.assertTrue(bridge.discovery_ready)
        self.assertTrue(bridge.subscription_state["discovered"])
        return bridge

    def test_queue_rollback_commit_and_explicit_delayed_execution(self):
        other = self._ready("default", "default-route")
        bridge = self._ready("producer", "explicit-producer")
        self.assertEqual(other.pk, bridge.pk)
        sent = []
        with patch(
            "angee.jobs.enqueue.celery_app.send_task", side_effect=lambda task, **options: sent.append((task, options))
        ):
            with self.assertRaisesRegex(ValueError, "rollback"):
                with transaction.atomic(using="producer"):
                    with transaction.atomic(using="default"):
                        bridge.request_sync()
                    self.assertEqual(sent, [])
                    raise ValueError("rollback")
            bridge.refresh_from_db(using="producer")
            self.assertEqual(bridge.sync_stage, "idle")
            self.assertEqual(sent, [])
            with transaction.atomic(using="producer"):
                with transaction.atomic(using="default"):
                    bridge.request_sync()
                self.assertEqual(sent, [])
            self.assertEqual(len(sent), 1)
        payload = sent[0][1]["kwargs"]
        self.assertEqual(payload["using"], "producer")
        self.assertEqual(sync_bridge_now(**payload)["items"], 1)
        bridge.refresh_from_db(using="producer")
        other.refresh_from_db(using="default")
        self.assertEqual(bridge.sync_stage, "completed")
        self.assertEqual(other.sync_stage, "idle")
        Imported = apps.get_model("routed_integrate", "ImportedRecord")
        Link = apps.get_model("integrate", "RecordLink")
        with database_context("producer"), system_context(reason="tests.integrate.routing.application"):
            self.assertEqual(Imported.objects.db_manager("producer").get().value, 7)
            self.assertFalse(Imported.objects.db_manager("default").exists())
            applied = Link.objects.db_manager("producer").get(external_key="remote:applied")
            self.assertEqual(applied.applied_snapshot._state.db, "producer")
            self.assertEqual(applied.resolve_target()._state.db, "producer")
        with patch("angee.jobs.enqueue.celery_app.send_task") as send:
            other.request_sync()
        legacy = dict(send.call_args.kwargs["kwargs"])
        legacy.pop("using")
        self.assertTrue(sync_bridge_now(**legacy)["ok"])
        other.refresh_from_db(using="default")
        self.assertEqual(other.sync_stage, "completed")

    def test_stream_failure_generation_and_actual_connection_guards(self):
        bridge = self._ready("producer", "retained-producer")
        Stream = apps.get_model("integrate", "SyncStream")
        Link = apps.get_model("integrate", "RecordLink")
        with (
            database_context("producer"),
            system_context(reason="tests.integrate.routing.stream"),
            changeset(fresh=True, using="producer"),
        ):
            stream = Stream.objects.current(bridge, StreamDefinition("records", kind=StreamKind.RECORD_REPLICA))
            self.assertEqual(stream._state.db, "producer")
            with transaction.atomic(using="default"):
                with self.assertRaisesRegex(RuntimeError, "page transaction"):
                    Stream.objects.lock_current(stream)
            with transaction.atomic(using="producer"):
                link = Link.objects.observe(stream, "remote:1")
                failure = link.record_failure(kind=SyncFailureKind.SEMANTIC, code="refused")
                self.assertEqual(failure.link_id, str(link.sqid))
                successor = Stream.objects.bump_generation(stream)
            with transaction.atomic(using="producer"):
                with self.assertRaisesRegex(RuntimeError, "retired stream generation"):
                    Stream.objects.lock_current(stream)
            with transaction.atomic(using="producer"):
                with self.assertRaisesRegex(RuntimeError, "outside a database transaction"):
                    advance_stream(successor, bridge.capability_impl)
            with transaction.atomic(using="default"):
                result = advance_stream(successor, bridge.capability_impl)
            self.assertTrue(result.exhausted)
            self.assertEqual(result.stream._state.db, "producer")
            bridge.merge_subscription_state(account_id="retained")
            bridge.report_progress("syncing", details={"source": "producer"})
            self.assertTrue(bridge.claim_dispatch(71, now=timezone.now()))
            self.assertFalse(bridge.settle_dispatch(72))
            self.assertTrue(bridge.settle_dispatch(71))
            bridge.refresh_from_db(using="producer")
            self.assertEqual(bridge.subscription_state["account_id"], "retained")
            self.assertEqual(bridge.sync_progress["details"], {"source": "producer"})

    def test_native_local_lock_namespaces_follow_alias(self):
        key = record_lock_key("routed_integrate.RoutedBridge", 1, "sync")
        with task_lock(key, using="producer") as producer:
            self.assertTrue(producer)
            self.assertTrue(task_lock_is_held(key, using="producer"))
            self.assertFalse(task_lock_is_held(key, using="default"))
            with task_lock(key, using="default") as default:
                self.assertTrue(default)
            with task_lock(key, using="producer") as duplicate:
                self.assertFalse(duplicate)
        self.assertFalse(task_lock_is_held(key, using="producer"))

    def test_session_store_and_exit_probe_follow_selected_alias(self):
        default = self._ready("default", "default-store")
        producer = self._ready("producer", "producer-store")
        self.assertEqual(default.sqid, producer.sqid)
        with TemporaryDirectory() as directory, override_settings(ANGEE_DATA_DIR=directory):
            default_path = live_module.session_store_path(default)
            producer_path = live_module.session_store_path(producer)
            self.assertEqual(default_path, Path(directory) / "routing" / str(default.sqid))
            self.assertNotEqual(default_path, producer_path)
            self.assertEqual(producer_path, live_module.session_store_path(producer))
            self.assertEqual(producer_path.parent.parent.name, ".databases")
            self.assertEqual(len(producer_path.parent.name), 64)
            for path in (default_path, producer_path):
                path.mkdir(parents=True)
                (path / "retained").write_text("session")
            live_module.reset_session_store(producer)
            self.assertFalse(producer_path.exists())
            self.assertTrue((default_path / "retained").exists())
        with patch.object(
            live_module, "task_locks_are_cross_process", wraps=live_module.task_locks_are_cross_process
        ) as capability:
            with self.assertRaises(ImproperlyConfigured):
                live_module.await_session_exit(producer)
            capability.assert_called_once_with(using="producer")


def test_native_integrate_producer_contract(tmp_path: Path):
    addon = tmp_path / "addons" / "example" / "routed_integrate"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(addon, "example.routed_integrate", depends_on=("angee.integrate",))
    (addon / "models.py").write_text(
        "from django.db import models\n"
        "from angee.base.fields.implementation import ImplClassField\n"
        "from angee.base.models.base import AngeeDataModel\n"
        "from angee.provenance.contrib.angee.history import HistoryMixin\n"
        "from angee.integrate.models import Bridge\n"
        "from tests.test_integrate_routing import RoutingBackend\n"
        "class RoutedBridge(Bridge, inherits='integrate.Integration'):\n"
        "    connection_impl = ImplClassField(RoutingBackend, default='routing')\n"
        "    live_impl_field = 'connection_impl'\n"
        "    class Meta:\n        abstract = True\n        rebac_resource_type = 'routing/bridge'\n"
        "class ImportedRecord(HistoryMixin, AngeeDataModel, concrete=True):\n"
        "    key = models.CharField(max_length=80, unique=True)\n"
        "    value = models.IntegerField(default=0)\n"
        "    class Meta:\n        abstract = True\n        rebac_resource_type = 'routing/imported'\n"
    )
    (addon / "permissions.zed").write_text(
        "// revision: 1\n"
        "definition routing/bridge {\n"
        "    relation integration: integrate/integration // rebac:field=integration_ptr\n"
        "    relation admin: angee/role // rebac:const=admin\n"
        "    permission create = admin->member\n"
        "    permission read = integration->read + admin->member\n"
        "    permission write = integration->write + admin->member\n"
        "    permission delete = integration->write + admin->member\n"
        "    permission write__owner = integration->transfer\n"
        "}\n"
        "definition routing/imported {\n"
        "    relation admin: angee/role // rebac:const=admin\n"
        "    permission create = admin->member\n"
        "    permission read = admin->member\n"
        "    permission write = admin->member\n"
        "    permission delete = admin->member\n"
        "}\n"
    )
    (addon / "autoconfig.py").write_text(
        'SETTINGS = {"DATABASES.producer": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}, '
        '"ANGEE_ROUTING_BACKEND_CLASSES": {"routing": "tests.test_integrate_routing.RoutingBackend"}}\n'
    )
    run_composed_tests(
        tmp_path,
        "tests.test_integrate_routing.RoutingContract",
        app="example.routed_integrate",
        addon_dirs=(tmp_path / "addons",),
    )
