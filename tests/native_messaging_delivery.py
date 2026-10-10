"""Delivery and feed contracts against the actual composed model graph."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from graphql import GraphQLInputObjectType
from rebac import PermissionDenied, actor_context, system_context
from rebac.roles import grant as grant_role

from angee.graphql.actions import ActionSelectionInput
from angee.graphql.ids import PublicID
from angee.graphql.schema import GraphQLSchemas
from angee.integrate.errors import IntegrationError
from angee.integrate.signals import binding_finished
from angee.integrate.streams import StreamDefinition, StreamPage
from angee.jobs.enqueue import celery_app
from angee.messaging import delivery
from angee.messaging.backends import DeliveryOutcome, ParsedHandle, ParsedMessage, ParsedPart
from angee.messaging.events import message_ingested
from angee.posts import mcp_tools
from angee.posts.backends import FeedBackend, FeedChannelBackend, ParsedPost
from angee.posts.ingest import land_posts
from angee.posts.models import validate_reply_hold

Message = apps.get_model("messaging", "Message")
Channel = apps.get_model("messaging", "Channel")
Feed = apps.get_model("posts", "Feed")
Handle = apps.get_model("parties", "Handle")


class DeliveryFixtures(TransactionTestCase):
    """Shared native graph and transport fixtures for delivery contract groups."""

    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        self.broker = self.enterContext(patch.object(celery_app, "send_task"))
        with system_context(reason="delivery fixtures"):
            self.operator = get_user_model().objects.create_user(username="delivery-operator")
            grant_role(actor=self.operator, role="angee/role:admin")
            self.agent = get_user_model().objects.create_user(username="delivery-replier")
            vendor = apps.get_model("integrate", "Vendor").objects.create(slug="delivery", display_name="Delivery")
            sender = Handle.objects.upsert(platform="youtube", value="publisher")
            self.feed = Feed.objects.create(
                vendor=vendor, owner=self.operator, display_name="Discussion", backend_class="feed",
                feed_backend_class="manual", external_id="account", handle=sender,
                live_since=timezone.now() - timedelta(days=1),
            )
            self.channel = Channel.objects.get(pk=self.feed.pk)
        self.channel.with_actor(self.operator).grant_record_access("reader", self.agent)
        self.channel.with_actor(self.operator).grant_record_access("replier", self.agent)
        self.backend = SimpleNamespace(
            deliver=Mock(return_value=DeliveryOutcome(accepted=True, provider_id="published")),
            delivery_lock=lambda: nullcontext(True), close=Mock(),
        )
        self.enterContext(patch.object(Channel, "backend", property(lambda row: self.backend)))

    def post(self, key, *, parent="", sent_at=None, hidden=False):
        return ParsedPost(
            ParsedMessage(external_id=key, platform="youtube", direction="inbound", subject="Discussion",
                          in_reply_to=parent, sent_at=sent_at or timezone.now(), body=ParsedPart(text="Comment")),
            is_original_post=not parent, hidden=hidden,
        )

    def comment(self):
        with system_context(reason="comment fixtures"):
            return land_posts(self.feed, [self.post("root"), self.post("comment", parent="root")],
                              owner_id=self.operator.pk)[1]

    def outbound(self, **values):
        with system_context(reason="outbound fixtures"):
            return Message.objects.create(
                channel_id=self.feed.pk, direction="outbound", platform="youtube", status="draft",
                external_id="client", **values,
            )

    def selection(self, message):
        return [ActionSelectionInput(id=PublicID(message.public_id), expected_revision=message.revision)]

    def run_delivery(self, message):
        return delivery.run_message_delivery(message._meta.label_lower, message.pk, message.external_id)


class DeliveryContracts(DeliveryFixtures):
    """Real REBAC, row state, creation replay, projections and enqueue timing."""

    def test_exact_contract_and_none_safe_transience(self):
        self.assertEqual(tuple(DeliveryOutcome.__dataclass_fields__), ("accepted", "provider_id"))
        for accepted, provider_id in ((False, "published"), (True, " ")):
            with self.subTest(accepted=accepted, provider_id=provider_id), self.assertRaises(TypeError):
                DeliveryOutcome(accepted=accepted, provider_id=provider_id)
        error = delivery.TransientDeliveryError(retry_after=None)
        self.assertIsInstance(error, IntegrationError)
        self.assertTrue(error.transient)
        self.assertIsNone(error.retry_after)

    def test_hold_field_refuses_nonfinite_or_negative_operator_values(self):
        for hours in (-1, float("inf"), float("nan")):
            with self.subTest(hours=hours), self.assertRaises(ValidationError):
                validate_reply_hold(hours)

    def test_body_projection_is_read_scoped_and_bounds_utf8_without_splitting_characters(self):
        comment = self.comment()
        with actor_context(self.agent):
            reply = comment.reply_to_comment(body="€" * 2000, actor=self.agent)
            text = reply.body_text(max_bytes=4096)
            self.assertEqual(len(text.encode("utf-8")), 4095)
            self.assertEqual(text, "€" * 1365)
        with system_context(reason="unrelated reader fixture"):
            visitor = get_user_model().objects.create_user(username="unrelated-reader")
        with actor_context(visitor), self.assertRaises(PermissionDenied):
            reply.with_actor(visitor).body_text()

    def test_approval_only_reply_is_exact_once_and_agent_cannot_release(self):
        comment = self.comment()
        with system_context(reason="published counter fixture"):
            comment.thread.refresh_from_db()
            published_count = comment.thread.message_count
        with actor_context(self.agent):
            reply = comment.reply_to_comment(body="A reply", actor=self.agent, creation_key="turn")
            replay = comment.reply_to_comment(body="A reply", actor=self.agent, creation_key="turn")
            self.assertEqual(reply.pk, replay.pk)
            self.assertEqual(reply.status, "draft")
            self.assertIsNone(reply.scheduled_at)
            self.assertFalse(self.channel.with_actor(self.agent).has_access("write"))
            results = Message.objects.send_held_drafts(self.selection(reply), actor=self.agent)
            self.assertFalse(results[0].ok)
        with system_context(reason="approval release check"):
            self.assertEqual(Message.objects.release_held_messages(now=timezone.now() + timedelta(days=365)), 0)
            comment.thread.refresh_from_db()
            self.assertEqual(comment.thread.message_count, published_count)
        self.assertFalse(self.broker.called)
        with actor_context(self.operator):
            self.assertTrue(Message.objects.send_held_drafts(self.selection(reply), actor=self.operator)[0].ok)
        self.assertEqual(self.broker.call_count, 1)
        with system_context(reason="published counter settlement"):
            self.run_delivery(reply)
            comment.thread.refresh_from_db()
            self.assertEqual(comment.thread.message_count, published_count + 1)

    def test_positive_and_zero_holds_insert_final_state(self):
        comment = self.comment()
        for hours, status in ((2, "draft"), (0, "queued")):
            with system_context(reason="reply policy fixture"):
                self.feed.reply_hold = hours
                self.feed.save(update_fields=("reply_hold", "updated_at"))
            with actor_context(self.agent):
                reply = comment.reply_to_comment(body=f"Reply {hours}", actor=self.agent, creation_key=f"turn-{hours}")
            self.assertEqual(reply.status, status)
            self.assertEqual(reply.scheduled_at is not None, hours > 0)
            with system_context(reason="next independent hold case"):
                Message.objects.get(pk=reply.pk).trash(reason="Next hold case")
        self.assertEqual(self.broker.call_count, 1)

    def test_enqueue_is_on_commit_and_only_leased_rows_recover(self):
        row = self.outbound()
        with system_context(reason="delivery enqueue"):
            with transaction.atomic():
                self.assertTrue(row.deliver())
                self.assertFalse(self.broker.called)
            self.assertTrue(self.broker.called)
            self.assertIsNotNone(row.delivery_lease_until)
            Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
            legacy = Message.objects.create(
                channel_id=self.feed.pk, direction="outbound", status="queued", external_id="legacy",
                scheduled_at=timezone.now() - timedelta(seconds=1),
            )
            self.assertEqual(Message.objects.release_held_messages(), 1)
            legacy.refresh_from_db()
            self.assertEqual(legacy.status, "queued")
            self.assertIsNone(legacy.delivery_lease_until)

    def test_due_retry_keeps_its_enqueue_lease_and_is_released_once(self):
        row = self.outbound()
        self.backend.deliver.side_effect = delivery.TransientDeliveryError(retry_after=timedelta(seconds=1))
        with system_context(reason="leased retry fixture"):
            row.deliver()
            self.run_delivery(row)
            row.refresh_from_db()
            self.assertGreater(row.delivery_lease_until, row.scheduled_at)
            Message.objects.filter(pk=row.pk).update(scheduled_at=timezone.now() - timedelta(seconds=1))
            before = self.broker.call_count
            self.assertEqual(Message.objects.release_held_messages(), 1)
            self.assertEqual(Message.objects.release_held_messages(), 0)
        self.assertEqual(self.broker.call_count - before, 1)

    def test_a_claimed_retry_is_not_reenqueued_while_its_provider_call_runs(self):
        row = self.outbound()
        def publish(message):
            self.assertIsNone(message.scheduled_at)
            self.assertEqual(Message.objects.release_held_messages(), 0)
            return DeliveryOutcome(accepted=True)
        self.backend.deliver.side_effect = publish
        with system_context(reason="active retry lease fixture"):
            row.deliver()
            Message.objects.filter(pk=row.pk).update(scheduled_at=timezone.now() - timedelta(seconds=1))
            self.assertTrue(self.run_delivery(row)["delivered"])
        self.assertEqual(self.broker.call_count, 1)

    def test_retry_hint_backoff_and_attempt_cap(self):
        row = self.outbound()
        self.backend.deliver.side_effect = delivery.TransientDeliveryError(retry_after=None)
        with system_context(reason="delivery retries"):
            for attempt in range(1, delivery.MAX_DELIVERY_ATTEMPTS + 1):
                Message.objects.filter(pk=row.pk).update(scheduled_at=None, delivery_lease_until=None)
                row.deliver()
                self.run_delivery(row)
                row.refresh_from_db()
                self.assertEqual(row.local_metadata["delivery_attempts"], attempt)
                if attempt < delivery.MAX_DELIVERY_ATTEMPTS:
                    self.assertEqual(row.status, "queued")
                    self.assertGreater(row.scheduled_at, timezone.now())
            self.assertEqual(row.status, "failed")
            self.assertIsNone(row.scheduled_at)
            self.assertIsNone(row.delivery_lease_until)

    def test_contention_does_not_consume_attempt_and_collision_is_sent(self):
        row = self.outbound()
        self.backend.delivery_lock = lambda: nullcontext(False)
        with system_context(reason="delivery contention"):
            row.deliver()
            self.assertEqual(self.run_delivery(row)["status"], "retry")
            row.refresh_from_db()
            self.assertNotIn("delivery_attempts", row.local_metadata)
            self.backend.deliver.assert_not_called()
            self.backend.delivery_lock = lambda: nullcontext(True)
            Message.objects.create(channel_id=self.feed.pk, external_id="published")
            Message.objects.filter(pk=row.pk).update(scheduled_at=None)
            self.assertTrue(self.run_delivery(row)["delivered"])
            row.refresh_from_db()
            self.assertEqual(row.status, "sent")
            self.assertEqual(row.external_id, "client")
            self.assertEqual(row.local_metadata["delivery_conflict"], "published")

    def test_bad_due_row_does_not_abort_batch_and_invalid_claim_fails(self):
        row = self.outbound(scheduled_at=timezone.now() - timedelta(seconds=1))
        with system_context(reason="release isolation"):
            second = Message.objects.create(channel_id=self.feed.pk, direction="outbound", status="draft",
                                            external_id="second", scheduled_at=row.scheduled_at)
            original = Message.validate_delivery
            def validate(message):
                if message.pk == row.pk:
                    raise ValidationError("Invalid envelope")
                return original(message)
            with patch.object(Message, "validate_delivery", validate):
                self.assertEqual(Message.objects.release_held_messages(), 1)
            row.refresh_from_db()
            second.refresh_from_db()
            self.assertEqual(row.status, "failed")
            self.assertEqual(second.status, "queued")
            Message.objects.filter(pk=second.pk).update(channel=None)
            self.assertIsNone(Message.objects.claim_delivery(second.pk, token=second.external_id))
            second.refresh_from_db()
            self.assertEqual(second.status, "failed")

    def test_revision_refusal_and_trash_restore_never_rearm(self):
        row = self.outbound()
        stale = self.selection(row)
        with system_context(reason="stale selection fixture"):
            row.preview = "Edited after review"
            row.save(update_fields=("preview", "updated_at"))
        with actor_context(self.operator):
            self.assertFalse(Message.objects.send_held_drafts(stale, actor=self.operator)[0].ok)
            self.assertTrue(Message.objects.discard_held_drafts(self.selection(row), actor=self.operator)[0].ok)
        with system_context(reason="restored delivery check"):
            row.refresh_from_db()
            row.restore()
            self.assertEqual(row.status, "failed")
            self.assertIsNone(row.scheduled_at)
            self.assertIsNone(row.delivery_lease_until)
            self.assertEqual(Message.objects.release_held_messages(), 0)
        self.assertFalse(self.broker.called)

    def test_trash_during_publish_retains_the_provider_acceptance_without_rearming(self):
        row = self.outbound()
        def publish(message):
            message.trash(reason="Removed during publish.")
            return DeliveryOutcome(accepted=True, provider_id="published")
        self.backend.deliver.side_effect = publish
        with system_context(reason="publish removal race"):
            row.deliver()
            self.assertTrue(self.run_delivery(row)["delivered"])
            row.refresh_from_db()
            self.assertTrue(row.is_trashed)
            self.assertEqual(row.status, "sent")
            self.assertEqual(row.external_id, "published")
            self.assertIsNone(row.delivery_lease_until)
            row.restore()
            self.assertEqual(Message.objects.release_held_messages(), 0)

    @override_settings(ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS=["agent_turn", "import_source", "delivery_token"])
    def test_trusted_provenance_and_echo_preserve_outbound_identity(self):
        comment = self.comment()
        with actor_context(self.agent):
            row = comment.reply_to_comment(body="Authored reply", actor=self.agent, local={"agent_turn": "turn"})
        with system_context(reason="trusted provenance"):
            Message.objects.write_protected_local([row], {"import_source": "archive"}, reason="archive provenance")
            parsed = ParsedMessage(external_id=row.external_id, platform="youtube", direction="inbound",
                                   sender=ParsedHandle(platform="youtube", value="wrong"), body=ParsedPart(text="Echo"),
                                   metadata={"local": {"agent_turn": "overwrite", "import_source": "overwrite"}})
            Message.objects.ingest([parsed], channel=self.feed)
            row.refresh_from_db()
            self.assertEqual(row.direction, "outbound")
            self.assertEqual(row.sender_id, self.feed.handle_id)
            self.assertEqual(row.body_text(), "Authored reply")
            self.assertEqual(row.local_metadata["agent_turn"], "turn")
            self.assertEqual(row.local_metadata["import_source"], "archive")

    def test_overlay_precedes_event_and_hidden_posts_never_trigger(self):
        seen = []
        def observe(sender, instance, **kwargs):
            seen.append((instance.external_id, instance.is_original_post, instance.is_trashed))
        message_ingested.connect(observe)
        self.addCleanup(message_ingested.disconnect, observe)
        with system_context(reason="live feed classification"):
            backend = FeedBackend(self.feed)
            backend.apply_record(None, self.post("old", sent_at=self.feed.live_since - timedelta(seconds=1)))
            backend.apply_record(None, self.post("live"))
            hidden = self.post("hidden", hidden=True)
            backend.apply_record(None, hidden)
            self.assertTrue(Message.objects.with_external_ids(["hidden"]).get().is_trashed)
            backend.apply_record(None, replace(hidden, hidden=False))
            self.assertFalse(Message.objects.with_external_ids(["hidden"]).get().is_trashed)
        self.assertEqual(seen, [("live", True, False)])

    def test_comment_overlay_preserves_the_original_posts_thread_url(self):
        with system_context(reason="public URL fixture"):
            root = replace(self.post("url-root"), subject_url="https://example.test/post")
            _, comment = land_posts(self.feed, [root, self.post("url-comment", parent="url-root")],
                                    owner_id=self.operator.pk)
            comment.thread.refresh_from_db()
            self.assertEqual(comment.thread.subject_url, root.subject_url)

    def test_binding_finish_stamps_horizon_once(self):
        with system_context(reason="binding horizon fixture"):
            self.feed.live_since = None
            self.feed.save(update_fields=("live_since", "updated_at"))
            binding_finished.send(sender=type(self.feed), instance=self.feed)
            self.feed.refresh_from_db()
            first = self.feed.live_since
            binding_finished.send(sender=type(self.feed), instance=self.feed)
            self.feed.refresh_from_db()
        self.assertIsNotNone(first)
        self.assertEqual(self.feed.live_since, first)

    def test_feed_transport_uses_integrates_concrete_capability_and_refuses_a_missing_feed(self):
        with system_context(reason="feed transport identity"):
            transport = FeedChannelBackend(self.channel)
            self.addCleanup(transport.close)
            self.assertIsInstance(transport.bridge, Feed)
            self.assertEqual(transport.bridge.pk, self.feed.pk)
            channel = Channel.objects.create(
                vendor=self.feed.vendor, display_name="Content source", backend_class="feed",
            )
            with self.assertRaisesMessage(IntegrationError, "This channel's feed source is unavailable."):
                FeedChannelBackend(channel)

    def test_feed_pages_commit_durable_cursor_and_quarantine_one_bad_record(self):
        fixture = self

        class PagedFeed(FeedBackend):
            def streams(self, *, deadline=None):
                return (StreamDefinition(key="history", partition="account"),)

            def extract(self, stream, page_bound, *, deadline=None):
                page = stream.cursor.get("page", 0)
                records = [fixture.post("first"), fixture.post("")] if page == 0 else [fixture.post("second")]
                return StreamPage(records=records, cursor={"page": page + 1}, exhausted=page > 0)

        with system_context(reason="paged feed fixture"), patch.object(Feed, "backend", property(PagedFeed)):
            self.assertEqual(self.feed.sync(), 2)
            stream = apps.get_model("integrate", "SyncStream").objects.get(integration_id=self.feed.pk, key="history")
            self.assertEqual(stream.cursor, {"page": 2})
            self.assertTrue(stream.has_completed_baseline())
            failures = apps.get_model("integrate", "SyncDiscrepancy").objects.filter(stream=stream)
            self.assertEqual(list(failures.values_list("code", flat=True)), ["missing_external_id"])
            self.assertEqual(Message.objects.with_external_ids(["first", "second"]).count(), 2)

    def test_native_schema_exposes_filter_and_body_but_no_message_state_inputs(self):
        self.comment()
        schemas = GraphQLSchemas.from_discovery()
        schema = schemas.build("console")
        text = str(schema)
        self.assertIn("is_original_post", text)
        self.assertIn("body_text", text)
        self.assertNotIn("message_text(", text)
        self.assertNotIn("update_messages_by_pk", text)
        resource = next(item for item in schemas.resources("console") if item.model_label == "messaging.Message")
        self.assertEqual(resource.update_fields, ())
        self.assertIsNone(resource.roots.update_name)
        for name, type_ in schemas.graphql_schema("console").type_map.items():
            if ("Message" in name and isinstance(type_, GraphQLInputObjectType)
                    and ("SetInput" in name or "InsertInput" in name)):
                self.assertNotIn("metadata", type_.fields)
                self.assertNotIn("status", type_.fields)
        with actor_context(self.agent):
            result = schema.execute_sync("{ messages(where: {is_original_post: {_eq: false}}) { id body_text } }",
                                         context_value=SimpleNamespace(request=SimpleNamespace(user=self.agent)))
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(len(result.data["messages"]), 1)

    def test_tools_use_one_actor_scoped_root_and_sqid_argument(self):
        with patch.object(mcp_tools, "register_graphql_tools") as register:
            mcp_tools.register(Mock())
        tools = register.call_args.args[1]
        self.assertEqual([tool.name for tool in tools], ["read_comment_thread", "read_message_text"])
        self.assertTrue(all(tool.operation == "messages_by_pk" and tool.id_arg == "id" for tool in tools))
        self.assertEqual(tools[1].fields, ("body_text",))
