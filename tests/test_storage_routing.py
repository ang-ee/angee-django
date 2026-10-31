"""Native storage factories and row lifecycle retain the selected database."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings
from rebac import actor_context, database_context, system_context

from angee.base.testing.packages import write_addon_distribution
from angee.provenance.context import changeset
from angee.storage import exceptions
from angee.storage.managers import FileAttachmentManager
from angee.storage.signals import file_finalized
from tests.composed_host import run_composed_tests


class StorageWriteRouter:
    def db_for_write(self, model, **hints):
        if model._meta.app_label == "storage":
            return "producer"
        return None


class StorageRoutingContract(TransactionTestCase):
    __test__ = False
    databases = {"default", "producer"}

    def setUp(self):
        self.root = TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.drives = {}
        self.users = {}
        for alias in ("default", "producer"):
            call_command("rebac", "sync", database=alias, verbosity=0)
            with (
                database_context(alias),
                system_context(reason="tests.storage.routing.seed"),
                changeset(fresh=True, using=alias),
            ):
                user = get_user_model().objects.db_manager(alias).create_user(username="owner")
                backend = (
                    apps.get_model("storage", "Backend")
                    .objects.db_manager(alias)
                    .create(
                        slug="local",
                        label="Local",
                        backend_class="local",
                        backend_config={"root": str(Path(self.root.name) / alias)},
                    )
                )
                self.drives[alias] = (
                    apps.get_model("storage", "Drive")
                    .objects.db_manager(alias)
                    .create(
                        slug="assets",
                        name=alias,
                        backend=backend,
                        prefix="assets",
                        owner=user,
                    )
                )
                self.users[alias] = user

    def _draft(self, alias, name="draft.txt", **kwargs):
        File = apps.get_model("storage", "File")
        with actor_context(self.users[alias]), changeset(fresh=True, using=alias):
            return File.objects.db_manager(alias).draft(
                filename=name,
                drive_id=str(self.drives[alias].sqid),
                **kwargs,
            )

    def test_draft_attachment_rollback_and_colliding_default_rows(self):
        File = apps.get_model("storage", "File")
        Attachment = apps.get_model("storage", "FileAttachment")
        default = self._draft("default")
        producer = self._draft("producer")
        self.assertEqual(default.pk, producer.pk)
        with (
            actor_context(self.users["producer"]),
            database_context("producer"),
            changeset(fresh=True, using="producer"),
        ):
            producer.issue_upload_token()
            producer._consume_upload_token()
            producer.merge_metadata({"selected": True})
            edge = Attachment.objects.attach(producer, self.drives["producer"])
            self.assertEqual(edge._state.db, "producer")
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with transaction.atomic(using="producer"):
                    self._draft("producer", "rollback.txt", record=self.drives["producer"])
                    raise RuntimeError("rollback")
        with actor_context(self.users["producer"]), changeset(fresh=True, using="producer"):
            with self.assertRaises(exceptions.UploadRecordDenied):
                self._draft("producer", "cross-db.txt", record=self.drives["default"])
            with self.assertRaises(ValueError):
                Attachment.objects.attach(producer, self.drives["default"])
        with database_context("producer"), system_context(reason="tests.storage.routing.inspect"):
            self.assertEqual(File.objects.using("producer").count(), 1)
            self.assertEqual(Attachment.objects.using("producer").count(), 1)
        with database_context("default"), system_context(reason="tests.storage.routing.inspect"):
            default.refresh_from_db(using="default")
            self.assertEqual(default.metadata, {})
            self.assertEqual(default.upload_envelope, {})
            self.assertFalse(Attachment.objects.using("default").exists())

    def test_native_factories_route_writes_and_enclose_attachment_failure(self):
        File = apps.get_model("storage", "File")
        Drive = apps.get_model("storage", "Drive")
        Attachment = apps.get_model("storage", "FileAttachment")
        with (
            override_settings(DATABASE_ROUTERS=[StorageWriteRouter()]),
            actor_context(self.users["producer"]),
            changeset(fresh=True, using="producer"),
        ):
            row = File.objects.draft(filename="routed.txt", drive_slug="assets")
            self.assertEqual(row._state.db, "producer")
            self.assertEqual(row.drive.name, "producer")
        original = FileAttachmentManager._attach

        def refuse_after_insert(manager, *args, **kwargs):
            self.assertTrue(connections["producer"].in_atomic_block)
            self.assertFalse(connections["default"].in_atomic_block)
            original(manager, *args, **kwargs)
            raise RuntimeError("edge failure")

        with actor_context(self.users["producer"]), changeset(fresh=True, using="producer"):
            with patch.object(FileAttachmentManager, "_attach", refuse_after_insert):
                with self.assertRaisesRegex(RuntimeError, "edge failure"):
                    self._draft("producer", "refused.txt", record=self.drives["producer"])
                with self.assertRaisesRegex(RuntimeError, "edge failure"):
                    Attachment.objects.attach(row, self.drives["producer"])
        with database_context("producer"), system_context(reason="tests.storage.routing.inspect"):
            self.assertEqual(File.objects.using("producer").count(), 1)
            self.assertFalse(Attachment.objects.using("producer").exists())
            with transaction.atomic(using="producer"):
                created = Drive.objects.db_manager("producer").create_on_default_backend(slug="new", name="New")
                self.assertEqual(created._state.db, "producer")
                transaction.set_rollback(True, using="producer")
            self.assertFalse(Drive.objects.using("producer").filter(slug="new").exists())
        self.assertFalse(File._base_manager.using("default").exists())

    def test_row_callback_waits_for_selected_commit_and_rollback_discards_it(self):
        row = self._draft("producer")
        seen = []

        def finalized(sender, instance, **kwargs):
            seen.append(instance._state.db)

        file_finalized.connect(finalized, weak=False)
        self.addCleanup(file_finalized.disconnect, finalized)
        with actor_context(self.users["producer"]), changeset(fresh=True, using="producer"):
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with transaction.atomic(using="producer"):
                    row.merge_metadata({"rolled_back": True})
                    with transaction.atomic(using="default"):
                        row._emit_finalized()
                    self.assertEqual(seen, [])
                    raise RuntimeError("rollback")
            self.assertEqual(seen, [])
            with transaction.atomic(using="producer"):
                row._emit_finalized()
                self.assertEqual(seen, [])
            self.assertEqual(seen, ["producer"])
        with database_context("producer"), system_context(reason="tests.storage.routing.inspect"):
            row.refresh_from_db(using="producer")
            self.assertEqual(row.metadata, {})

    def test_factory_io_finalize_and_callback_follow_producer(self):
        File = apps.get_model("storage", "File")
        default = self._draft("default")
        seen = []

        def finalized(sender, instance, **kwargs):
            seen.append(instance._state.db)

        file_finalized.connect(finalized, weak=False)
        self.addCleanup(file_finalized.disconnect, finalized)
        original = type(self.drives["producer"].storage).save
        with (
            patch("angee.storage.backends.LocalBackend.save", autospec=True) as save,
            changeset(fresh=True, using="producer"),
        ):
            # Exercise the real backend implementation while proving IO is outside atomic.

            def write(storage, *args, **kwargs):
                self.assertFalse(connections["producer"].in_atomic_block)
                return original(storage, *args, **kwargs)

            save.side_effect = write
            file, created = File.objects.db_manager("producer").get_or_ingest_bytes(
                b"selected bytes",
                filename="producer.txt",
                drive_slug="assets",
            )
        self.assertTrue(created)
        self.assertEqual(file._state.db, "producer")
        self.assertEqual(file.content_hash, hashlib.sha256(b"selected bytes").hexdigest())
        self.assertEqual(seen, ["producer"])
        with database_context("default"), system_context(reason="tests.storage.routing.inspect"):
            default.refresh_from_db(using="default")
            self.assertEqual(default.upload_state, "draft")


def test_native_storage_selected_database(tmp_path):
    addon = tmp_path / "addons" / "example" / "storage_routing"
    addon.mkdir(parents=True)
    (addon / "__init__.py").touch()
    write_addon_distribution(addon, "example.storage_routing", depends_on=("angee.storage",))
    (addon / "autoconfig.py").write_text(
        'SETTINGS = {"DATABASES.producer": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}\n'
    )
    (addon / "permissions.extends.zed").write_text(
        "definition storage/file_attachment {\n"
        " relation routing_drive: storage/drive // rebac:field=target\n"
        " permission target_write = routing_drive->write\n}\n"
    )
    run_composed_tests(
        tmp_path,
        "tests.test_storage_routing.StorageRoutingContract",
        app="example.storage_routing",
        addon_dirs=(tmp_path / "addons",),
    )
