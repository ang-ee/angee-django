"""Cross-addon contracts exercised through the emitted model graph."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.db.models.signals import pre_delete
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from rebac import PermissionDenied, actor_context, system_context

from angee.base.mixins import CreationKeyConflict
from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.errors import IntegrationError
from angee.integrate.impl import AdapterContractError
from angee.integrate.locks import bridge_advisory_lock
from angee.knowledge.mcp_tools import register
from angee.knowledge.models import RecordBindingManager
from angee.messaging.backends import ParsedHandle
from angee.posts.backends import FeedBackend
from angee.posts.ingest import land_posts
from angee.posts.models import CommentAnswered
from tests.native_messaging_delivery import Channel, DeliveryFixtures, Feed, Message


class ReplyJoins(DeliveryFixtures):
    def test_reply_evidence_covers_active_states_and_excludes_failed_or_discarded(self):
        comment = self.comment()
        self.assertFalse(comment.reply_state())
        reply = comment.reply_to_comment(body="Answer", actor=self.agent, creation_key="answer")
        with system_context(reason="reply evidence states"):
            for status, trashed, expected in (("draft", False, True), ("queued", False, True),
                                             ("sent", False, True), ("failed", False, False),
                                             ("draft", True, False)):
                Message.objects.filter(pk=reply.pk).update(status=status, is_trashed=trashed)
                self.assertEqual(comment.reply_state(), expected)

    def test_native_and_channel_authored_replies_are_answer_evidence(self):
        comment = self.comment()
        with system_context(reason="native reply evidence"):
            native = replace(self.post("native", parent="comment"), message=replace(
                self.post("native", parent="comment").message,
                sender=ParsedHandle(platform="youtube", value="publisher"),
            ))
            land_posts(self.feed, [native], owner_id=self.operator.pk)
            self.assertTrue(comment.reply_state())
            with self.assertRaisesMessage(CommentAnswered, "already answered"):
                comment.reply_to_comment(body="Stale inference", actor=self.agent, creation_key="late-run")
            Message.objects.filter(pk=comment.pk).update(sender_id=self.feed.handle_id)
            comment.refresh_from_db()
            self.assertTrue(comment.reply_state())

    def test_replay_precedes_another_runs_admission_and_checks_the_fingerprint(self):
        comment = self.comment()
        first = comment.reply_to_comment(body="Answer", actor=self.agent, creation_key="first-run")
        replay = comment.reply_to_comment(body="Answer", actor=self.agent, creation_key="first-run")
        self.assertEqual(first.pk, replay.pk)
        with self.assertRaisesMessage(CommentAnswered, "already answered"):
            comment.reply_to_comment(body="Another", actor=self.agent, creation_key="second-run")
        with self.assertRaises(CreationKeyConflict) as refusal:
            comment.reply_to_comment(body="Changed", actor=self.agent, creation_key="first-run")
        self.assertNotIsInstance(refusal.exception, CommentAnswered)

    def test_unreadable_comment_refuses_with_permission_denied(self):
        comment = self.comment()
        with system_context(reason="unreadable comment actor"):
            visitor = get_user_model().objects.create_user(username="comment-visitor")
        with self.assertRaisesMessage(PermissionDenied, "Comment read access"):
            comment.reply_to_comment(body="Answer", actor=visitor, creation_key="unreadable")

    def test_discovery_applies_identity_and_empty_discovery_preserves_it(self):
        with system_context(reason="feed identity discovery"):
            adapter = FeedBackend(self.feed)
            adapter.apply_discovery(ConnectionDiscovery(data={
                "external_id": "discovered", "display_name": "Discovered feed",
                "handle": ParsedHandle(platform="youtube", value="discovered-publisher", external_id="publisher-id"),
            }))
            self.feed.refresh_from_db()
            self.assertEqual((self.feed.external_id, self.feed.display_name), ("discovered", "Discovered feed"))
            self.assertEqual(self.feed.handle.external_id, "publisher-id")
            with patch.object(Feed, "save") as save:
                adapter.apply_discovery(ConnectionDiscovery())
                save.assert_not_called()
            with self.assertRaisesMessage(AdapterContractError, "discover_connection"):
                adapter.discover_connection(None)

    def test_discovery_truncates_labels_and_validates_identifiers(self):
        with system_context(reason="feed discovery validation"):
            adapter = FeedBackend(self.feed)
            adapter.apply_discovery(ConnectionDiscovery(data={"display_name": "x" * 300, "external_id": "identity"}))
            self.feed.refresh_from_db()
            self.assertEqual(self.feed.display_name, "x" * 255)
            with self.assertRaises(ValidationError):
                adapter.apply_discovery(ConnectionDiscovery(data={"external_id": "x" * 513}))
            self.feed.refresh_from_db()
            self.assertEqual(self.feed.external_id, "identity")

    def test_quota_honours_each_callers_limit_on_an_existing_period(self):
        quota = apps.get_model("posts", "Quota")
        with system_context(reason="quota reserve"):
            self.assertTrue(quota.objects.consume(integration=self.feed, units=70, limit=100))
            self.assertFalse(quota.objects.consume(integration=self.feed, units=20, limit=80))
            self.assertTrue(quota.objects.consume(integration=self.feed, units=20, limit=100))
            self.assertFalse(quota.objects.consume(integration=self.feed, units=11, limit=100))
            row = quota.objects.get(integration=self.feed)
            self.assertEqual(row.quota_used, 90)
            self.assertTrue(quota.objects.consume(integration=self.feed, units=20, limit=120))
            self.assertEqual(quota.objects.get(pk=row.pk).quota_used, 110)

    def test_binding_receiver_runs_after_commit_and_stamps_once(self):
        with system_context(reason="committed binding horizon"):
            Feed.objects.filter(pk=self.feed.pk).update(live_since=None)
            with transaction.atomic():
                self.feed.attach_credential(self.feed.credential)
                self.feed.refresh_from_db()
                self.assertIsNone(self.feed.live_since)
            self.feed.refresh_from_db()
            first = self.feed.live_since
            self.assertIsNotNone(first)
            self.feed.attach_credential(self.feed.credential)
            self.feed.refresh_from_db()
            self.assertEqual(self.feed.live_since, first)

    def test_parent_addressed_inbound_uses_the_concrete_capability_lock(self):
        def inbound():
            close_old_connections()
            try:
                with system_context(reason="parent addressed webhook"):
                    return Channel.objects.get(pk=self.feed.pk).dispatch_inbound({})
            finally:
                close_old_connections()

        with patch.object(Feed, "verify_webhook", return_value=True), patch.object(Feed, "handle_webhook") as land:
            with bridge_advisory_lock(self.feed) as acquired, ThreadPoolExecutor(max_workers=1) as pool:
                self.assertTrue(acquired)
                with self.assertRaises(IntegrationError) as refusal:
                    pool.submit(inbound).result(timeout=10)
                self.assertTrue(refusal.exception.transient)
                land.assert_not_called()
            self.assertTrue(inbound())
            land.assert_called_once()

    def test_inbound_refuses_an_enclosing_transaction(self):
        with transaction.atomic(), self.assertRaisesMessage(IntegrationError, "autocommit"):
            self.feed.dispatch_inbound({})


class MemoryJoins(DeliveryFixtures):
    def test_create_page_tool_writes_only_a_granted_vault(self):
        with actor_context(self.agent):
            memory = apps.get_model("knowledge", "Vault").objects.create_for(self.agent, name="Writable memory")
        with actor_context(self.operator):
            curated = apps.get_model("knowledge", "Vault").objects.create_for(self.operator, name="Curated")
            curated.grant_record_access("viewer", self.agent)
        server = FastMCP("memory contracts")
        register(server)
        tools = {tool.name: tool for tool in async_to_sync(server.list_tools)()}
        with actor_context(self.agent):
            created = async_to_sync(tools["create_page"].run)({"vault": memory.sqid, "title": "Topic"})
            self.assertEqual(created.structured_content["title"], "Topic")
            with self.assertRaises(ToolError):
                async_to_sync(tools["create_page"].run)({"vault": curated.sqid, "title": "Forbidden"})
        with system_context(reason="vault tool boundary"):
            self.assertFalse(apps.get_model("knowledge", "Page").objects.filter(vault=curated).exists())

    def test_ensure_page_is_role_scoped_and_record_deletion_trashes_memory_only(self):
        binding = apps.get_model("knowledge", "RecordBinding")
        page_model = apps.get_model("knowledge", "Page")
        with actor_context(self.operator):
            vault = apps.get_model("knowledge", "Vault").objects.create_for(self.operator, name="Memory")
            record = apps.get_model("projects", "Task").objects.create(title="Contact context")
            page = binding.objects.ensure_page(
                record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="Contact", actor=self.operator,
            )
            again = binding.objects.ensure_page(
                record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="Changed", actor=self.operator,
            )
            self.assertEqual(page.pk, again.pk)
            related = page_model.objects.create_in(vault, title="Related reference")
            binding.objects.upsert(target=record, page=related)
            record.delete()
            page.refresh_from_db()
            related.refresh_from_db()
            self.assertTrue(page.is_trashed)
            self.assertFalse(related.is_trashed)
            self.assertFalse(binding.objects.filter(page=page).exists())

    def test_record_deletion_accepts_an_already_trashed_memory_page(self):
        binding = apps.get_model("knowledge", "RecordBinding")
        with actor_context(self.operator):
            vault = apps.get_model("knowledge", "Vault").objects.create_for(self.operator, name="Removed memory")
            record = apps.get_model("projects", "Task").objects.create(title="Removed contact")
            page = binding.objects.ensure_page(
                record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="Removed", actor=self.operator,
            )
            page.trash()
            trashed_at = page.trashed_at
            record.delete()
            page.refresh_from_db()
            self.assertTrue(page.is_trashed)
            self.assertEqual(page.trashed_at, trashed_at)
            self.assertFalse(binding.objects.filter(page=page).exists())

    def test_record_deletion_trashes_memory_bound_during_pre_delete(self):
        binding = apps.get_model("knowledge", "RecordBinding")
        handle_model = apps.get_model("parties", "Handle")
        with actor_context(self.operator):
            vault = apps.get_model("knowledge", "Vault").objects.create_for(self.operator, name="Late memory")
            page = apps.get_model("knowledge", "Page").objects.create_in(vault, title="Late")
        with system_context(reason="test late memory target"):
            # An unthreaded target: no messaging teardown locks its row before the DELETE.
            record = handle_model.objects.upsert(
                platform="youtube", value="late-memory", created_by_id=self.operator.pk,
            )

        def bind_before_delete(sender, instance, **kwargs):
            if instance.pk == record.pk:
                binding.objects.upsert(target=record, page=page, role=RecordBindingManager.MEMORY_ROLE)

        pre_delete.connect(bind_before_delete, sender=handle_model, weak=False)
        try:
            with system_context(reason="test late memory deletion"):
                handle_model.objects.get(pk=record.pk).delete()
        finally:
            pre_delete.disconnect(bind_before_delete, sender=handle_model)
        with system_context(reason="test late memory result"):
            page.refresh_from_db()
            self.assertTrue(page.is_trashed)
            self.assertFalse(binding.objects.filter(page=page).exists())

    def test_ensure_selects_the_first_untrashed_page_and_refuses_an_unreadable_binding(self):
        binding = apps.get_model("knowledge", "RecordBinding")
        with actor_context(self.operator):
            vault = apps.get_model("knowledge", "Vault").objects.create_for(self.operator, name="Bound memory")
            record = apps.get_model("projects", "Task").objects.create(title="Bound contact")
            first = binding.objects.ensure_page(
                record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="First", actor=self.operator,
            )
            second = apps.get_model("knowledge", "Page").objects.create_in(vault, title="Second")
            binding.objects.upsert(target=record, page=second, role=RecordBindingManager.MEMORY_ROLE)
            self.assertEqual(binding.objects.ensure_page(
                record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="Unused", actor=self.operator,
            ).pk, first.pk)
            first.trash()
            self.assertEqual(binding.objects.ensure_page(
                record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="Unused", actor=self.operator,
            ).pk, second.pk)
        with self.assertRaises(PermissionDenied):
            binding.objects.ensure_page(
                record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="Hidden", actor=self.agent,
            )
        with system_context(reason="hidden memory not duplicated"):
            self.assertEqual(binding.objects.for_record(record).count(), 2)


@skipUnless(connection.vendor == "postgresql", "Independent PostgreSQL locks required.")
class PostgreSQLJoins(DeliveryFixtures):
    def test_concurrent_memory_ensures_share_one_page(self):
        bindings = apps.get_model("knowledge", "RecordBinding")
        with actor_context(self.operator):
            vault = apps.get_model("knowledge", "Vault").objects.create_for(self.operator, name="Concurrent memory")
            record = apps.get_model("projects", "Task").objects.create(title="Memory target")
        barrier = Barrier(2)

        def ensure():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return bindings.objects.ensure_page(
                    record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="Memory", actor=self.operator,
                ).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = [future.result(timeout=15) for future in (pool.submit(ensure), pool.submit(ensure))]
        self.assertEqual(first, second)
        with system_context(reason="memory race result"):
            self.assertEqual(bindings.objects.for_record(record, role=RecordBindingManager.MEMORY_ROLE).count(), 1)

    def test_unthreaded_record_deletion_waits_for_memory_binding_write(self):
        bindings = apps.get_model("knowledge", "RecordBinding")
        handle = apps.get_model("parties", "Handle")
        page_model = apps.get_model("knowledge", "Page")
        with actor_context(self.operator):
            vault = apps.get_model("knowledge", "Vault").objects.create_for(self.operator, name="Deleting memory")
            record = handle.objects.upsert(platform="youtube", value="memory-contact")
        locked, release, deleting = Event(), Event(), Event()
        create_in = type(page_model.objects).create_in

        def pause_creation(manager, *args, **kwargs):
            locked.set()
            self.assertTrue(release.wait(timeout=10))
            return create_in(manager, *args, **kwargs)

        def delete():
            close_old_connections()
            try:
                with actor_context(self.operator):
                    deleting.set()
                    handle.objects.get(pk=record.pk).delete()
            finally:
                close_old_connections()

        def ensure():
            close_old_connections()
            try:
                return bindings.objects.ensure_page(
                    record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="Raced", actor=self.operator,
                )
            finally:
                close_old_connections()

        with patch.object(type(page_model.objects), "create_in", pause_creation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                creating = pool.submit(ensure)
                self.assertTrue(locked.wait(timeout=10))
                deletion = pool.submit(delete)
                self.assertTrue(deleting.wait(timeout=10))
                try:
                    with self.assertRaises(TimeoutError):
                        deletion.result(timeout=0.5)
                finally:
                    release.set()
                page = creating.result(timeout=10)
                deletion.result(timeout=10)
        with system_context(reason="deleted memory race result"):
            page.refresh_from_db()
            self.assertTrue(page.is_trashed)
            self.assertFalse(bindings.objects.for_record(record).exists())

    def test_unthreaded_record_deleted_first_refuses_every_binding_writer(self):
        bindings = apps.get_model("knowledge", "RecordBinding")
        handle = apps.get_model("parties", "Handle")
        with actor_context(self.operator):
            vault = apps.get_model("knowledge", "Vault").objects.create_for(self.operator, name="Deleted memory")
            record = handle.objects.upsert(platform="youtube", value="deleted-memory-contact")
            page = apps.get_model("knowledge", "Page").objects.create_in(vault, title="Existing")
        deleted, release = Event(), Event()
        teardown = type(bindings.objects).teardown_for_record

        def pause_teardown(manager, target):
            if type(target) is handle and target.pk == record.pk:
                deleted.set()
                self.assertTrue(release.wait(timeout=10))
            return teardown(manager, target)

        def delete():
            close_old_connections()
            try:
                with actor_context(self.operator):
                    handle.objects.get(pk=record.pk).delete()
            finally:
                close_old_connections()

        def bind():
            close_old_connections()
            try:
                with actor_context(self.operator):
                    return bindings.objects.upsert(target=record, page=page, role=RecordBindingManager.MEMORY_ROLE)
            finally:
                close_old_connections()

        with patch.object(type(bindings.objects), "teardown_for_record", pause_teardown):
            with ThreadPoolExecutor(max_workers=2) as pool:
                deletion = pool.submit(delete)
                self.assertTrue(deleted.wait(timeout=10))
                writing = pool.submit(bind)
                try:
                    with self.assertRaises(TimeoutError):
                        writing.result(timeout=0.5)
                finally:
                    release.set()
                deletion.result(timeout=10)
                with self.assertRaises(handle.DoesNotExist):
                    writing.result(timeout=10)
        with actor_context(self.operator):
            with self.assertRaises(handle.DoesNotExist):
                bindings.objects.ensure_page(
                    record, role=RecordBindingManager.MEMORY_ROLE, vault=vault, title="Refused", actor=self.operator,
                )
            self.assertFalse(bindings.objects.for_record(record).exists())
            self.assertEqual(apps.get_model("knowledge", "Page").objects.filter(vault=vault).count(), 1)

    def test_two_runs_can_prepare_only_one_reply(self):
        comment = self.comment()
        barrier = Barrier(2)

        def prepare(key):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                row = Message._base_manager.get(pk=comment.pk)
                try:
                    return row.reply_to_comment(body="Answer", actor=self.agent, creation_key=key).pk
                except ValidationError:
                    return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(prepare, ("run-one", "run-two")))
        self.assertEqual(sum(reply is not None for reply in replies), 1)
        with system_context(reason="reply race result"):
            self.assertEqual(Message.objects.filter(parent=comment, direction="outbound").count(), 1)
