"""Native storage factories and row lifecycle retain the selected database."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connections, models, transaction
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, database_context, system_context

from angee.base.testing.packages import write_addon_distribution
from angee.iam.graphql.relations import with_record_reference_access
from angee.provenance.context import changeset
from angee.storage import exceptions
from angee.storage.managers import FileAttachmentManager
from angee.storage.signals import create_trash_folder, file_finalized
from angee.storage.uploads import DOWNLOAD_TOKEN_SALT, UPLOAD_TOKEN_SALT
from angee.storage.views import download, upload
from tests.composed_host import run_composed_tests


class StorageWriteRouter:
    def db_for_write(self, model, **hints):
        if model._meta.app_label == "storage":
            return "producer"
        return None


class StorageRoutingContract(TransactionTestCase):
    __test__ = False
    databases = {"default", "producer"}
    reset_sequences = True

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

    def test_user_trash_signal_uses_producer_and_rolls_back_with_user(self):
        Folder = apps.get_model("storage", "Folder")
        with self.assertRaises(ValueError):
            create_trash_folder(get_user_model(), self.users["producer"], created=True, using="default")
        for alias in ("default", "producer"):
            self.assertEqual(
                Folder._base_manager.using(alias).filter(owner_id=self.users[alias].pk, smart_kind="trash").count(), 1
            )
        default_before = set(Folder._base_manager.using("default").values_list("pk", flat=True))
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with changeset(fresh=True, using="producer"), transaction.atomic(using="producer"):
                user = get_user_model().objects.db_manager("producer").create_user(username="rolled-back-user")
                self.assertTrue(
                    Folder._base_manager.using("producer").filter(owner_id=user.pk, smart_kind="trash").exists()
                )
                raise RuntimeError("rollback")
        with database_context("producer"), system_context(reason="tests.storage.routing.inspect"):
            self.assertFalse(get_user_model().objects.using("producer").filter(username="rolled-back-user").exists())
        self.assertEqual(Folder._base_manager.using("producer").count(), 1)
        self.assertEqual(set(Folder._base_manager.using("default").values_list("pk", flat=True)), default_before)

    def _draft(self, alias, name="draft.txt", **kwargs):
        File = apps.get_model("storage", "File")
        with actor_context(self.users[alias]), changeset(fresh=True, using=alias):
            return File.objects.db_manager(alias).draft(
                filename=name,
                drive_id=str(self.drives[alias].sqid),
                **kwargs,
            )

    def test_factory_constraint_validation_reads_hidden_rows_on_selected_alias(self):
        File = apps.get_model("storage", "File")
        default = self._draft("default")
        digest = "c" * 64
        with (
            database_context("producer"),
            system_context(reason="tests.storage.routing.hidden_constraint"),
            changeset(fresh=True, using="producer"),
        ):
            hidden_owner = get_user_model().objects.db_manager("producer").create_user(username="hidden-owner")
            hidden = File.objects.db_manager("producer").create(
                drive=self.drives["producer"],
                owner=hidden_owner,
                filename="hidden.txt",
                content_hash=digest,
                storage_path="hidden/reservation",
                visibility="record",
            )
        self.assertEqual(default.pk, hidden.pk)
        self.assertFalse(hidden.with_actor(self.users["producer"]).has_access("read"))
        with patch("angee.storage.managers.secrets.token_hex", return_value=digest):
            with self.assertRaisesRegex(exceptions.UploadError, "invalid file request") as refused:
                self._draft("producer", "collision.txt")
        self.assertIsInstance(refused.exception.__cause__, ValidationError)
        self.assertEqual(File._base_manager.using("producer").count(), 1)
        default.refresh_from_db(using="default")
        self.assertEqual(default.filename, "draft.txt")
        self.assertNotEqual(default.content_hash, digest)
        created = self._draft("producer", "fresh.txt")
        self.assertEqual(created._state.db, "producer")
        self.assertEqual(created.created_by_id, self.users["producer"].pk)
        self.assertFalse(created.is_sudo())
        self.assertEqual(created.actor(), hidden.actor())

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

    def test_reference_projection_clone_uses_the_selected_connection(self):
        Attachment = apps.get_model("storage", "FileAttachment")
        edges = {}
        for alias in ("default", "producer"):
            with (
                database_context(alias),
                actor_context(self.users[alias]),
                changeset(fresh=True, using=alias),
            ):
                edges[alias] = Attachment.objects.db_manager(alias).attach(
                    self._draft(alias), self.drives[alias],
                )
        self.assertEqual(edges["default"].pk, edges["producer"].pk)
        with database_context("default"), system_context(reason="tests.reference.routing.revoke"):
            self.drives["default"].owner = None
            self.drives["default"].save(using="default", update_fields=["owner"])
        with actor_context(self.users["producer"]), database_context("default"):
            # A plain native source isolates target projection from source-row
            # admission, which remains the caller's existing responsibility.
            prepared = with_record_reference_access(models.QuerySet(model=Attachment, using="default"))
            self.assertEqual(list(prepared.values_list("_angee_record_readable", flat=True)), [False])
            with CaptureQueriesContext(connections["default"]) as default_queries:
                selected = prepared.all().using("producer")
                self.assertEqual(list(selected.values_list("_angee_record_readable", flat=True)), [True])
            self.assertEqual(list(default_queries), [])

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

    def test_signed_upload_token_routes_unbound_http_and_keeps_legacy_default_identity(self):
        File = apps.get_model("storage", "File")
        default = self._draft("default")
        producer = self._draft("producer")
        self.assertEqual(default.sqid, producer.sqid)
        with changeset(fresh=True, using="default"):
            default_token = default.issue_upload_token()
        with changeset(fresh=True, using="producer"):
            token = producer.issue_upload_token()
        self.assertNotIn("database", signing.loads(default_token, salt=UPLOAD_TOKEN_SALT))
        self.assertEqual(signing.loads(token, salt=UPLOAD_TOKEN_SALT)["database"], "producer")
        self.assertEqual(File.objects.for_upload_token(token)._state.db, "producer")
        with self.assertRaises(exceptions.UploadDenied):
            File.objects.db_manager("default").for_upload_token(token)
        for invalid in (
            token + "tampered",
            signing.dumps({"file": str(producer.sqid), "nonce": "n", "database": "unknown"}, salt=UPLOAD_TOKEN_SALT),
        ):
            with self.assertRaises(exceptions.UploadDenied):
                File.objects.for_upload_token(invalid)
        request = RequestFactory().put(
            "/?token=" + token, data=b"producer upload", content_type="application/octet-stream"
        )
        with actor_context(self.users["producer"]), changeset(fresh=True, using="producer"):
            response = upload(request)
        self.assertEqual(response.status_code, 200, response.content)
        with database_context("default"), system_context(reason="tests.storage.routing.inspect"):
            default.refresh_from_db(using="default")
            self.assertFalse(default.upload_envelope["used"])
            self.assertFalse(default.storage.exists(default.storage_path))
        with database_context("producer"), system_context(reason="tests.storage.routing.inspect"):
            producer.refresh_from_db(using="producer")
            self.assertTrue(producer.upload_envelope["used"])
            with producer.storage.open(producer.storage_path, "rb") as stream:
                self.assertEqual(stream.read(), b"producer upload")
        # Historical unsigned-DB payloads identify default even when current writes route elsewhere.
        legacy = signing.dumps(
            {"file": str(default.sqid), "nonce": default.upload_envelope["nonce"]}, salt=UPLOAD_TOKEN_SALT
        )
        with override_settings(DATABASE_ROUTERS=[StorageWriteRouter()]):
            self.assertEqual(File.objects.for_upload_token(legacy)._state.db, "default")

    def test_signed_download_token_http_serves_only_its_database_and_rechecks_actor(self):
        File = apps.get_model("storage", "File")
        rows = {}
        for alias in ("default", "producer"):
            with changeset(fresh=True, using=alias):
                rows[alias] = File.objects.db_manager(alias).ingest_bytes(
                    alias.encode(), filename="token.txt", drive_slug="assets"
                )
        self.assertEqual(rows["default"].sqid, rows["producer"].sqid)
        for alias in ("default", "producer"):
            token = rows[alias].issue_download_token(self.users[alias])
            response = download(RequestFactory().get("/?token=" + token), "cosmetic.txt")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), alias.encode())
            response.close()
            if alias == "default":
                self.assertNotIn("database", signing.loads(token, salt=DOWNLOAD_TOKEN_SALT))
            else:
                self.assertEqual(File.objects.for_download_token(token)._state.db, "producer")
                with self.assertRaises(exceptions.UploadDenied):
                    File.objects.db_manager("default").for_download_token(token)
                with database_context("producer"), system_context(reason="tests.storage.routing.revoke"):
                    self.drives["producer"].owner = None
                    self.drives["producer"].save(using="producer", update_fields=["owner"])
                response = download(RequestFactory().get("/?token=" + token), "cosmetic.txt")
                self.assertEqual(response.status_code, 403)

    def test_external_index_and_folder_path_reject_foreign_parents_before_writes(self):
        File = apps.get_model("storage", "File")
        Folder = apps.get_model("storage", "Folder")
        folders = {}
        for alias in ("default", "producer"):
            with (
                database_context(alias),
                system_context(reason="tests.storage.routing.parents"),
                changeset(fresh=True, using=alias),
            ):
                folders[alias] = Folder._base_manager.db_manager(alias).create(
                    pk=1000, drive=self.drives[alias], name="existing"
                )
                self.assertEqual(
                    Folder.objects.db_manager(alias).ensure_path(self.drives[alias], ("existing",)).pk, 1000
                )
        self.assertEqual(folders["default"].pk, folders["producer"].pk)
        values = dict(
            storage_path="external/key",
            filename="external.txt",
            content_hash=hashlib.sha256(b"external").hexdigest(),
            size_bytes=8,
        )
        manager = File.objects.db_manager("producer")
        with self.assertRaises(ValueError):
            manager.index_external(drive=self.drives["default"], **values)
        with self.assertRaises(ValueError):
            manager.index_external(drive=self.drives["producer"], folder=folders["default"], **values)
        with self.assertRaises(ValueError):
            Folder.objects.db_manager("producer").ensure_path(self.drives["default"], ("refused",))
        with self.assertRaises(ValueError):
            Folder.objects.db_manager("producer").ensure_path(
                self.drives["producer"], ("existing", "refused"), cache={("existing",): folders["default"]}
            )
        for alias in ("default", "producer"):
            self.assertFalse(File._base_manager.using(alias).exists())
            self.assertFalse(Folder._base_manager.using(alias).filter(name="refused").exists())
        with changeset(fresh=True, using="producer"):
            row = manager.index_external(drive=self.drives["producer"], folder=folders["producer"], **values)
        with self.assertRaises(ValueError):
            manager.index_external(drive=self.drives["default"], metadata={"refused": True}, **values)
        with database_context("producer"), system_context(reason="tests.storage.routing.inspect"):
            row.refresh_from_db(using="producer")
            self.assertEqual(row.metadata, {})
        self.assertFalse(File._base_manager.using("default").exists())

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
