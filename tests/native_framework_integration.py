"""Cross-addon contracts exercised through the emitted model graph."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from rebac import PermissionDenied, actor_context, system_context

from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.errors import IntegrationError
from angee.integrate.impl import AdapterContractError
from angee.integrate.locks import bridge_advisory_lock
from angee.knowledge.mcp_tools import register
from angee.messaging.backends import ParsedHandle
from angee.models import CreationKeyConflict
from angee.posts.backends import FeedBackend
from angee.posts.ingest import land_posts
from angee.posts.models import CommentAnswered
from tests.native_messaging_delivery import Channel, DeliveryFixtures, Feed, Message


class ReplyJoins(DeliveryFixtures):
    def test_reply_evidence_covers_active_states_and_excludes_failed_or_discarded(self):
        comment = self.comment()
        self.assertFalse(comment.reply_state())
        reply = comment.reply_to_comment(body="Answer", actor=self.agent, idempotency_key="answer")
        with system_context(reason="reply evidence states"):
            for status, trashed, expected in (
                ("draft", False, True),
                ("queued", False, True),
                ("sent", False, True),
                ("failed", False, False),
                ("draft", True, False),
            ):
                Message.objects.filter(pk=reply.pk).update(status=status, is_trashed=trashed)
                self.assertEqual(comment.reply_state(), expected)

    def test_native_and_channel_authored_replies_are_answer_evidence(self):
        comment = self.comment()
        with system_context(reason="native reply evidence"):
            native = replace(
                self.post("native", parent="comment"),
                message=replace(
                    self.post("native", parent="comment").message,
                    sender=ParsedHandle(platform="youtube", value="publisher"),
                ),
            )
            land_posts(self.feed, [native], owner_id=self.operator.pk)
            self.assertTrue(comment.reply_state())
            with self.assertRaisesMessage(CommentAnswered, "already answered"):
                comment.reply_to_comment(body="Stale inference", actor=self.agent, idempotency_key="late-run")
            Message.objects.filter(pk=comment.pk).update(sender_id=self.feed.handle_id)
            comment.refresh_from_db()
            self.assertTrue(comment.reply_state())

    def test_replay_precedes_another_runs_admission_and_checks_the_fingerprint(self):
        comment = self.comment()
        first = comment.reply_to_comment(body="Answer", actor=self.agent, idempotency_key="first-run")
        replay = comment.reply_to_comment(body="Answer", actor=self.agent, idempotency_key="first-run")
        self.assertEqual(first.pk, replay.pk)
        with self.assertRaisesMessage(CommentAnswered, "already answered"):
            comment.reply_to_comment(body="Another", actor=self.agent, idempotency_key="second-run")
        with self.assertRaises(CreationKeyConflict) as refusal:
            comment.reply_to_comment(body="Changed", actor=self.agent, idempotency_key="first-run")
        self.assertNotIsInstance(refusal.exception, CommentAnswered)

    def test_unreadable_comment_refuses_with_permission_denied(self):
        comment = self.comment()
        with system_context(reason="unreadable comment actor"):
            visitor = get_user_model().objects.create_user(username="comment-visitor")
        with self.assertRaisesMessage(PermissionDenied, "Comment read access"):
            comment.reply_to_comment(body="Answer", actor=visitor, idempotency_key="unreadable")

    def test_discovery_applies_identity_and_empty_discovery_preserves_it(self):
        with system_context(reason="feed identity discovery"):
            adapter = FeedBackend(self.feed)
            adapter.apply_discovery(
                ConnectionDiscovery(
                    data={
                        "external_id": "discovered",
                        "display_name": "Discovered feed",
                        "handle": ParsedHandle(
                            platform="youtube", value="discovered-publisher", external_id="publisher-id"
                        ),
                    }
                )
            )
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
            period = quota.objects.open_period(integration=self.feed, scope="example:project:shared")
            self.assertTrue(quota.objects.consume(period=period, units=70, limit=100))
            self.assertFalse(quota.objects.consume(period=period, units=20, limit=80))
            self.assertTrue(quota.objects.consume(period=period, units=20, limit=100))
            self.assertFalse(quota.objects.consume(period=period, units=11, limit=100))
            row = quota.objects.get(pk=period.pk)
            self.assertEqual(row.quota_used, 90)
            self.assertTrue(quota.objects.consume(period=period, units=20, limit=120))
            row.refresh_from_db()
            self.assertEqual(row.quota_used, 110)

    def test_discovery_receiver_runs_after_commit_and_stamps_once(self):
        with system_context(reason="committed discovery horizon"):
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
                    pool.submit(copy_context().run, inbound).result(timeout=10)
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


@skipUnless(connection.vendor == "postgresql", "Independent PostgreSQL locks required.")
class PostgreSQLJoins(DeliveryFixtures):
    def test_two_runs_can_prepare_only_one_reply(self):
        comment = self.comment()
        barrier = Barrier(2)

        def prepare(key):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                row = Message._base_manager.get(pk=comment.pk)
                try:
                    return row.reply_to_comment(body="Answer", actor=self.agent, idempotency_key=key).pk
                except ValidationError:
                    return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(prepare, ("run-one", "run-two")))
        self.assertEqual(sum(reply is not None for reply in replies), 1)
        with system_context(reason="reply race result"):
            self.assertEqual(Message.objects.filter(parent=comment, direction="outbound").count(), 1)
