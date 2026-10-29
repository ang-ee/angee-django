"""Delivery and feed contracts against the actual composed model graph."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from smtplib import SMTPDataError, SMTPServerDisconnected
from socket import gaierror
from ssl import SSLError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anymail.backends.base_requests import AnymailRequestsBackend
from anymail.exceptions import (
    AnymailAPIError,
    AnymailInvalidAddress,
    AnymailRecipientsRefused,
    AnymailUnsupportedFeature,
)
from celery.app.routes import MapRoute, Router
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.signals import post_save
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from graphql import GraphQLInputObjectType
from rebac import PermissionDenied, actor_context, system_context
from rebac.actors import is_sudo
from rebac.roles import grant as grant_role
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ConnectTimeout, ReadTimeout
from urllib3.exceptions import MaxRetryError, NameResolutionError, NewConnectionError, ProtocolError

from angee.base.models.locks import StaleRevisionError
from angee.graphql.actions import MAX_ACTION_SELECTION, ActionSelectionInput
from angee.graphql.ids import PublicID
from angee.graphql.schema import GraphQLSchemas
from angee.integrate.errors import IntegrationError, RequestNotSentError
from angee.integrate.http import RedirectOriginError, ResponseTooLargeError
from angee.integrate.signals import discovery_finished
from angee.integrate.states import StreamDefinition
from angee.integrate.streams import StreamPage
from angee.jobs.enqueue import celery_app
from angee.messaging import delivery
from angee.messaging.backends import DeliveryOutcome, DeliveryUncertain, ParsedHandle, ParsedMessage, ParsedPart
from angee.messaging.email import AnymailEmailChannelBackend
from angee.messaging.events import message_ingested
from angee.messaging.managers import DeliveryReconciliationBusy
from angee.posts import mcp_tools
from angee.posts.backends import RECONCILIATION_SKEW_MARGIN, FeedBackend, FeedChannelBackend, ParsedPost, RecordRefusal
from angee.posts.ingest import land_posts
from angee.posts.models import validate_reply_hold
from angee.provenance.context import changeset_model

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
            reconciles_delivery=lambda: False,
            delivery_visibility_delay=timedelta(),
        )
        self.original_channel_capability = Channel.can_reconcile_delivery
        self.original_channel_visibility_delay = Channel.delivery_visibility_delay
        self.enterContext(patch.object(Channel, "backend", property(lambda row: self.backend)))
        self.enterContext(patch.object(
            Channel, "can_reconcile_delivery", lambda row: self.backend.reconciles_delivery(),
        ))
        self.enterContext(patch.object(
            Channel, "delivery_visibility_delay", lambda row: self.backend.delivery_visibility_delay,
        ))

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
            return Message.objects.create(**{
                "channel_id": self.feed.pk, "direction": "outbound", "platform": "youtube", "status": "draft",
                "external_id": "client", **values,
            })

    def selection(self, message):
        return [ActionSelectionInput(id=PublicID(message.public_id), expected_revision=message.revision)]

    def run_delivery(self, message):
        return delivery.run_message_delivery(message._meta.label_lower, message.pk, message.external_id)


class FakeFeedBackend(FeedBackend):
    """A deterministic provider exercising the public publication and lookup hooks."""

    key = "fake"
    delivery_visibility_delay = timedelta()  # This provider's fake edge is immediately visible.

    def __init__(self, feed):
        super().__init__(feed)
        self.published = []
        self.lookups = []
        self.posts = 0
        self.publish_error = None
        self.lookup_error = None
        self.scope = "fake:project:shared"

    def quota_scope(self):
        return self.scope

    def publish_reply(self, message):
        self.posts += 1
        identity = f"provider-{self.posts}"
        self.published.append({"id": identity, "parent": message.top_level_comment().pk,
                               "author": self.bridge.handle_id,
                               "created_at": timezone.now().replace(microsecond=0), "body": message.body_text()})
        if self.publish_error is not None:
            raise self.publish_error
        return identity

    def find_published_reply(self, message, *, since):
        self.lookups.append((message.top_level_comment().pk, since))
        if self.lookup_error is not None:
            raise self.lookup_error
        for reply in sorted(self.published, key=lambda row: row["created_at"], reverse=True):
            if reply["created_at"] < since:
                break
            if (reply["parent"] == message.top_level_comment().pk and reply["author"] == self.bridge.handle_id
                    and reply["body"] == message.body_text()):
                return reply["id"]
        return None


class DeliveryContracts(DeliveryFixtures):
    """Real REBAC, row state, creation replay, projections and enqueue timing."""

    def test_exact_contract_and_none_safe_transience(self):
        self.assertEqual(tuple(DeliveryOutcome.__dataclass_fields__), ("accepted", "provider_id"))
        for accepted, provider_id in ((False, "published"), (True, " ")):
            with self.subTest(accepted=accepted, provider_id=provider_id), self.assertRaises(TypeError):
                DeliveryOutcome(accepted=accepted, provider_id=provider_id)
        error = IntegrationError(transient=True, retry_after=None)
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
            reply = comment.reply_to_comment(body="A reply", actor=self.agent, idempotency_key="turn")
            replay = comment.reply_to_comment(body="A reply", actor=self.agent, idempotency_key="turn")
            self.assertEqual(reply.pk, replay.pk)
            self.assertEqual(reply.status, "draft")
            self.assertIsNone(reply.scheduled_at)
            self.assertFalse(self.channel.with_actor(self.agent).has_access("write"))
            results = Message.objects.send_held_drafts(self.selection(reply), actor=self.agent)
            self.assertFalse(results[0].ok)
        with system_context(reason="approval release check"):
            self.assertEqual(Message.objects.release_held_messages(now=timezone.now() + timedelta(days=365)), 0)
            comment.thread.refresh_from_db()
            self.assertEqual(comment.thread.message_count, published_count + 1)
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
                reply = comment.reply_to_comment(
                    body=f"Reply {hours}", actor=self.agent, idempotency_key=f"turn-{hours}"
                )
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
        self.backend.deliver.side_effect = IntegrationError(retry_after=timedelta(seconds=1))
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
        self.backend.deliver.side_effect = IntegrationError(transient=True, retry_after=None)
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

    @override_settings(ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS=["agent_turn", "delivery_token"])
    def test_trusted_provenance_and_echo_preserve_outbound_identity(self):
        comment = self.comment()
        with actor_context(self.agent):
            row = comment.reply_to_comment(body="Authored reply", actor=self.agent, local={"agent_turn": "turn"})
        with system_context(reason="trusted provenance"):
            parsed = ParsedMessage(external_id=row.external_id, platform="youtube", direction="inbound",
                                   sender=ParsedHandle(platform="youtube", value="wrong"), body=ParsedPart(text="Echo"),
                                   metadata={"local": {"agent_turn": "overwrite"}})
            Message.objects.ingest([parsed], channel=self.feed)
            row.refresh_from_db()
            self.assertEqual(row.direction, "outbound")
            self.assertEqual(row.sender_id, self.feed.handle_id)
            self.assertEqual(row.body_text(), "Authored reply")
            self.assertEqual(row.local_metadata["agent_turn"], "turn")
            self.assertEqual(row.edit_history, [])

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

    def test_discovery_finish_stamps_horizon_once(self):
        with system_context(reason="discovery horizon fixture"):
            self.feed.live_since = None
            self.feed.save(update_fields=("live_since", "updated_at"))
            discovery_finished.send(sender=type(self.feed), instance=self.feed)
            self.feed.refresh_from_db()
            first = self.feed.live_since
            discovery_finished.send(sender=type(self.feed), instance=self.feed)
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

        with system_context(reason="paged feed fixture"), patch.object(
            Feed.feed_backend_class.field, "resolve_class", return_value=PagedFeed,
        ):
            self.assertEqual(self.feed.sync(), 2)
            stream = apps.get_model("integrate", "SyncStream").objects.get(integration_id=self.feed.pk, key="history")
            self.assertEqual(stream.cursor, {"page": 2})
            self.assertTrue(stream.has_completed_baseline())
            failures = stream.sync_failures(open_only=False)
            self.assertEqual([row.code for row in failures], ["missing_external_id"])
            self.assertEqual(Message.objects.with_external_ids(["first", "second"]).count(), 2)

    def test_provider_record_refusals_are_quarantined_separately_with_the_page_cursor(self):
        fixture = self

        class RefusingFeed(FeedBackend):
            def streams(self, *, deadline=None):
                return (StreamDefinition(key="activity"),)

            def extract(self, stream, page_bound, *, deadline=None):
                return StreamPage(
                    [fixture.post("landed"), RecordRefusal("bad-first", "invalid_record"),
                     RecordRefusal("bad-second", "invalid_record")], {"page": 1},
                )

        with system_context(reason="refused feed records"), patch.object(
            Feed.feed_backend_class.field, "resolve_class", return_value=RefusingFeed,
        ):
            self.assertEqual(self.feed.sync(), 1)
            stream = apps.get_model("integrate", "SyncStream").objects.get(integration_id=self.feed.pk)
            self.assertEqual(stream.cursor, {"page": 1})
            failures = stream.sync_failures(open_only=False)
            self.assertEqual(len(failures), 2)
            self.assertEqual({row.details["external_key"] for row in failures}, {"bad-first", "bad-second"})
            self.assertEqual({row.code for row in failures}, {"invalid_record"})
            self.assertTrue(Message.objects.with_external_ids(["landed"]).exists())

    def test_reobservation_budget_pauses_only_its_stream_and_resets_next_cycle(self):
        fixture = self
        cycles = []

        class BoundedFeed(FeedBackend):
            def streams(self, *, deadline=None):
                cycles.append(len(cycles) + 1)
                return (StreamDefinition(key="reobserve"), StreamDefinition(key="updates"))

            def extract(self, stream, page_bound, *, deadline=None):
                if stream.key == "updates":
                    self.consume_quota(1)
                    return StreamPage([fixture.post(f"live-{cycles[-1]}")], {"cycle": cycles[-1]})
                page = stream.cursor.get("page", 0)
                if not self.consume_reobserve_quota(1):
                    return StreamPage((), stream.cursor, exhausted=True)
                return StreamPage([fixture.post(f"observed-{page}")], {"page": page + 1}, exhausted=False)

        with system_context(reason="bounded separate reobservation"), patch.object(
            Feed.feed_backend_class.field, "resolve_class", return_value=BoundedFeed,
        ):
            self.feed.config = {"reobserve_units_per_cycle": 2}
            self.feed.save(update_fields=("config", "updated_at"))
            self.assertEqual(self.feed.sync(), 3)
            stream = apps.get_model("integrate", "SyncStream").objects.get(
                integration_id=self.feed.pk, key="reobserve",
            )
            self.assertEqual(stream.cursor, {"page": 2})
            self.assertEqual(self.feed.sync(), 3)
            stream.refresh_from_db()
            self.assertEqual(stream.cursor, {"page": 4})
            self.assertEqual(Message.objects.with_external_ids(["live-1", "live-2"]).count(), 2)
            period = BoundedFeed(self.feed).current_quota_period()
            self.assertEqual(period.quota_used, 6)

    def test_native_schema_exposes_filter_and_single_message_body_but_no_state_inputs(self):
        comment = self.comment()
        schemas = GraphQLSchemas.from_discovery()
        schema = schemas.build("console")
        text = str(schema)
        self.assertIn("is_original_post", text)
        self.assertIn("body_text", text)
        self.assertIn("message_text(", text)
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
            result = schema.execute_sync("{ messages(where: {is_original_post: {_eq: false}}) { id preview } }",
                                         context_value=SimpleNamespace(request=SimpleNamespace(user=self.agent)))
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(len(result.data["messages"]), 1)
        with actor_context(self.agent):
            result = schema.execute_sync("query($id: ID!) { message_text(id: $id) { body_text } }",
                                         variable_values={"id": str(comment.sqid)},
                                         context_value=SimpleNamespace(request=SimpleNamespace(user=self.agent)))
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data["message_text"]["body_text"], "Comment")

    def test_tools_use_actor_scoped_by_pk_projections_and_sqid_argument(self):
        with patch.object(mcp_tools, "register_graphql_tools") as register:
            mcp_tools.register(Mock())
        tools = register.call_args.args[1]
        self.assertEqual([tool.name for tool in tools], ["read_comment_thread", "read_message_text"])
        self.assertEqual([tool.operation for tool in tools], ["messages_by_pk", "message_text"])
        self.assertTrue(all(tool.id_arg == "id" for tool in tools))
        self.assertEqual(tools[1].fields, ("body_text",))

    def test_not_yet_due_hold_is_not_released(self):
        moment = timezone.now()
        row = self.outbound(scheduled_at=moment + timedelta(hours=2))
        with system_context(reason="future hold check"):
            self.assertEqual(Message.objects.release_held_messages(now=moment + timedelta(hours=1)), 0)
            row.refresh_from_db()
            self.assertEqual(row.status, "draft")
            self.assertIsNone(row.delivery_lease_until)

    def test_ingest_increments_without_thread_save_or_aggregate_and_recount_has_no_subquery(self):
        comment = self.comment()
        thread_model = apps.get_model("messaging", "Thread")
        saved = []

        def observe(sender, instance, **kwargs):
            saved.append(instance.pk)

        post_save.connect(observe, sender=thread_model)
        try:
            with system_context(reason="ingest counting query shape"), CaptureQueriesContext(connection) as inserted:
                land_posts(self.feed, [self.post("another-comment", parent="root")], owner_id=self.operator.pk)
        finally:
            post_save.disconnect(observe, sender=thread_model)
        self.assertEqual(saved, [])
        self.assertFalse(any("FILTER" in query["sql"] and "COUNT(" in query["sql"] for query in inserted))
        with system_context(reason="loss recount query shape"), CaptureQueriesContext(connection) as lost:
            comment.trash(reason="Removed comment")
        aggregates = [query["sql"] for query in lost if "FILTER" in query["sql"] and "COUNT(" in query["sql"]]
        self.assertEqual(len(aggregates), 1)
        self.assertNotIn(" IN (SELECT", aggregates[0].upper())
        with system_context(reason="counted row agreement"):
            comment.thread.refresh_from_db()
            self.assertEqual(
                comment.thread.message_count, Message.objects.for_thread(comment.thread).counted_in_thread().count(),
            )
        self.backend.deliver.assert_not_called()
        self.broker.assert_not_called()

    def test_insert_counter_eligibility_is_one_conditional_update(self):
        comment = self.comment()
        row = self.outbound(thread_id=comment.thread_id)
        with system_context(reason="one-statement insert counter"), CaptureQueriesContext(connection) as queries:
            Message.objects.advance_thread(row)
        statements = [query["sql"] for query in queries if Message._meta.db_table in query["sql"]]
        self.assertEqual(len(statements), 1, statements)
        self.assertIn("UPDATE", statements[0])
        self.assertIn("EXISTS", statements[0])
        with system_context(reason="insert counter result"):
            row.thread.refresh_from_db()
            self.assertEqual(row.thread.message_count, 3)

    def test_acceptance_without_settlement_becomes_uncertain_and_keeps_parent_answered(self):
        comment = self.comment()
        with actor_context(self.agent):
            row = comment.reply_to_comment(body="Accepted reply", actor=self.agent)
        with actor_context(self.operator):
            Message.objects.send_held_drafts(self.selection(row), actor=self.operator)
        with system_context(reason="lost acceptance settlement"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            self.assertIsNotNone(claimed.delivery_claim)
            self.assertFalse(connection.in_atomic_block)
            self.backend.deliver(claimed)  # The provider accepted; the worker dies here.
            Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
            before = self.broker.call_count
            self.assertEqual(Message.objects.release_held_messages(), 0)
            row.refresh_from_db()
            self.assertEqual(row.status, "uncertain")
            self.assertEqual(row.delivery_claim, claimed.delivery_claim)
            self.assertTrue(comment.reply_state())
            self.assertFalse(Message.objects.due_deliveries(now=timezone.now()).filter(pk=row.pk).exists())
            self.assertEqual(self.run_delivery(row)["status"], "uncertain")
        self.assertEqual(self.broker.call_count, before)
        self.assertEqual(self.backend.deliver.call_count, 1)

    def test_operator_acknowledgement_sends_without_another_effect_and_replier_is_denied(self):
        row = self.outbound()
        with system_context(reason="uncertain acknowledgement fixture"):
            row.deliver()
            claim = Message.objects.claim_delivery(row.pk, token=row.external_id)
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claim.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
            row.refresh_from_db()
        with actor_context(self.agent), self.assertRaises((PermissionDenied, Message.DoesNotExist)):
            row.acknowledge_delivery(actor=self.agent, expected_revision=row.revision)
        before = self.broker.call_count
        with actor_context(self.operator):
            acknowledged = row.acknowledge_delivery(actor=self.operator, expected_revision=row.revision)
            self.assertEqual(acknowledged.status, "sent")
            self.assertIsNotNone(acknowledged.sent_at)
            self.assertEqual(acknowledged.acknowledged_by_id, self.operator.pk)
        self.assertEqual(self.broker.call_count, before)
        self.backend.deliver.assert_not_called()

    def test_operator_retry_supersedes_the_claim_and_rejects_its_late_settlement(self):
        row = self.outbound()
        with system_context(reason="uncertain retry fixture"):
            row.deliver()
            old = Message.objects.claim_delivery(row.pk, token=row.external_id)
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=old.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
            row.refresh_from_db()
        with actor_context(self.operator), self.assertRaises(StaleRevisionError):
            row.retry_delivery(actor=self.operator, expected_revision=row.revision - 1)
        with actor_context(self.operator):
            retried = row.retry_delivery(actor=self.operator, expected_revision=row.revision)
            self.assertEqual(retried.status, "queued")
        with system_context(reason="superseded settlement check"):
            current = Message.objects.claim_delivery(row.pk, token=row.external_id)
            self.assertNotEqual(current.delivery_claim, old.delivery_claim)
            self.assertEqual(current.delivery_started_at, old.delivery_started_at)
            self.assertEqual(Message.objects.record_delivery(
                row.pk, token=row.external_id, claim=old.delivery_claim, outcome=DeliveryOutcome(accepted=True),
            ), "stale-delivery-token")
            self.assertEqual(Message.objects.record_delivery(
                row.pk, token=row.external_id, claim=current.delivery_claim, outcome=DeliveryOutcome(accepted=True),
            ), "sent")
        self.assertEqual(self.broker.call_count, 2)

    def test_native_transient_hint_retries_and_permanent_integration_error_fails(self):
        for error, expected in ((IntegrationError(retry_after=timedelta(seconds=30)), "queued"),
                                (IntegrationError("Permission refused."), "failed")):
            with self.subTest(status=expected), system_context(reason="native delivery refusal"):
                row = self.outbound(external_id=f"native-{expected}")
                self.backend.deliver.side_effect = error
                row.deliver()
                before = timezone.now()
                self.run_delivery(row)
                row.refresh_from_db()
                self.assertEqual(row.status, expected)
                if expected == "queued":
                    self.assertGreaterEqual(row.scheduled_at, before + timedelta(seconds=30))
                    self.assertLessEqual(row.scheduled_at, timezone.now() + timedelta(seconds=30))
                    self.assertIsNone(row.delivery_claim)

    def test_transport_validation_errors_are_uncertain_but_not_sent_and_false_are_definite(self):
        for index, (effect, expected) in enumerate((
            (ValidationError("Post-send response refusal."), "uncertain"),
            (ResponseTooLargeError("Post-send response limit."), "uncertain"),
            (RedirectOriginError("Post-send redirect."), "uncertain"),
            (RequestNotSentError(), "queued"), (False, "failed"),
        )):
            with self.subTest(expected=expected), system_context(reason="delivery phase classification"):
                row = self.outbound(external_id=f"phase-{index}")
                self.backend.deliver.side_effect = effect if isinstance(effect, Exception) else None
                self.backend.deliver.return_value = effect
                row.deliver()
                self.run_delivery(row)
                row.refresh_from_db()
                self.assertEqual(row.status, expected)
                self.assertEqual(row.delivery_possibly_accepted, expected == "uncertain")

    def test_failed_operator_retry_resets_attempts_and_keeps_the_message(self):
        with system_context(reason="failed operator retry fixture"):
            row = self.outbound(status="failed", metadata={"local": {"delivery_attempts": 4}})
        with actor_context(self.operator):
            retried = row.retry_delivery(actor=self.operator, expected_revision=row.revision)
        self.assertEqual(retried.pk, row.pk)
        self.assertEqual(retried.status, "queued")
        self.assertEqual(retried.local_metadata["delivery_attempts"], 0)
        self.assertEqual(retried.acknowledged_by_id, self.operator.pk)

    def test_console_graphql_acknowledgement_and_retry_apply_permissions_and_revisions(self):
        schema = GraphQLSchemas.from_discovery().build("console")
        for verb, starting, expected in (
            ("acknowledge_message_delivery", "uncertain", "sent"),
            ("retry_message_delivery", "uncertain", "queued"),
            ("retry_message_delivery", "failed", "queued"),
        ):
            with self.subTest(verb=verb, starting=starting), system_context(reason="delivery mutation fixture"):
                row = self.outbound(external_id=f"{verb}-{starting}")
                row.deliver()
                claim = Message.objects.claim_delivery(row.pk, token=row.external_id)
                error = DeliveryUncertain("Lost acknowledgement.") if starting == "uncertain" else IntegrationError()
                Message.objects.record_delivery(row.pk, token=row.external_id, claim=claim.delivery_claim, error=error)
                row.refresh_from_db()
            document = (f"mutation($id: ID!, $revision: Int!) {{ "
                        f"{verb}(id: $id, expected_revision: $revision) {{ ok }} }}")
            for actor, revision, allowed in (
                (self.agent, row.revision, False), (self.operator, row.revision - 1, False),
                (self.operator, row.revision, True),
            ):
                with actor_context(actor):
                    result = schema.execute_sync(
                        document, variable_values={"id": row.public_id, "revision": revision},
                        context_value=SimpleNamespace(request=SimpleNamespace(user=actor)),
                    )
                if actor == self.operator and revision != row.revision:
                    self.assertIsNotNone(result.errors)
                else:
                    self.assertIsNone(result.errors, result.errors)
                    self.assertEqual(result.data[verb]["ok"], allowed)
            with system_context(reason="delivery mutation result"):
                row.refresh_from_db()
                self.assertEqual(row.status, expected)
                self.assertEqual(row.acknowledged_by_id, self.operator.pk)

    def test_discarded_reply_idempotency_claim_does_not_reinsert(self):
        comment = self.comment()
        with actor_context(self.agent):
            reply = comment.reply_to_comment(body="Discard me", actor=self.agent, idempotency_key="discarded-turn")
        with actor_context(self.operator):
            Message.objects.discard_held_drafts(self.selection(reply), actor=self.operator)
        with actor_context(self.agent):
            try:
                replay = comment.reply_to_comment(body="Discard me", actor=self.agent, idempotency_key="discarded-turn")
            except ValidationError:
                pass
            else:
                self.assertEqual(replay.pk, reply.pk)
        with system_context(reason="discarded receipt result"):
            claim = changeset_model()._base_manager.get(key="discarded-turn")
            self.assertEqual(claim.created_row.pk, reply.pk)

    @override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
    def test_email_explicit_refusals_and_lost_acknowledgements_settle_distinctly(self):
        transport = AnymailEmailChannelBackend(self.channel)
        self.addCleanup(transport.close)
        cases = ((SMTPDataError(450, b"private provider text"), "queued"),
                 (SMTPDataError(550, b"private provider text"), "failed"),
                 (SMTPServerDisconnected("private transport text"), "uncertain"),
                 (TimeoutError("private timeout text"), "uncertain"),
                 (ConnectionRefusedError("private send text"), "uncertain"),
                 (AnymailAPIError("private payload", status_code=400), "failed"),
                 (AnymailAPIError("private payload", status_code=429), "queued"),
                 (AnymailAPIError("private payload", status_code=503), "uncertain"),
                 (AnymailAPIError("private payload"), "uncertain"),
                 (AnymailInvalidAddress("private address"), "failed"),
                 (AnymailRecipientsRefused("private refusal"), "failed"),
                 (AnymailUnsupportedFeature("private config"), "failed"))
        for index, (error, expected) in enumerate(cases):
            envelope = SimpleNamespace(from_email="sender@example.test", to=["reader@example.test"], cc=[], bcc=[],
                                       send=Mock(side_effect=error))
            with self.subTest(error=type(error).__name__), system_context(reason="email refusal fixture"), patch.object(
                transport, "email_message", return_value=envelope,
            ), patch("angee.messaging.email.get_connection", return_value=Mock()):
                row = self.outbound(external_id=f"email-{index}")
                self.backend.deliver.side_effect = transport.deliver
                row.deliver()
                self.run_delivery(row)
                row.refresh_from_db()
                self.assertEqual(row.status, expected)
                self.assertNotIn("private", row.local_metadata["delivery_error"])

    @override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
    def test_email_render_failure_precedes_the_effect_and_fails_definitely(self):
        transport = AnymailEmailChannelBackend(self.channel)
        self.addCleanup(transport.close)
        with system_context(reason="email render refusal"), patch.object(
            transport, "email_message", side_effect=OSError("private file failure"),
        ):
            row = self.outbound()
            self.backend.deliver.side_effect = transport.deliver
            row.deliver()
            self.assertEqual(self.run_delivery(row)["status"], "failed")
            row.refresh_from_db()
            self.assertNotIn("private", row.local_metadata["delivery_error"])

    @override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
    def test_email_open_failures_are_not_sent_and_always_close_the_connection(self):
        transport = AnymailEmailChannelBackend(self.channel)
        self.addCleanup(transport.close)
        for error in (gaierror("DNS"), SSLError("TLS"), TimeoutError("connect"), SMTPServerDisconnected("greeting")):
            envelope = SimpleNamespace(from_email="sender@example.test", to=["reader@example.test"], cc=[], bcc=[],
                                       send=Mock(return_value=1))
            connection_ = Mock()
            connection_.open.side_effect = error
            with self.subTest(error=type(error).__name__), patch.object(
                transport, "email_message", return_value=envelope,
            ), patch("angee.messaging.email.get_connection", return_value=connection_):
                with self.assertRaises(IntegrationError) as failure:
                    transport.deliver(self.outbound(external_id=type(error).__name__))
                self.assertTrue(failure.exception.transient)
            envelope.send.assert_not_called()
            connection_.close.assert_called_once()

    @override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
    def test_real_anymail_http_exceptions_retry_only_provable_connection_failures(self):
        transport = AnymailEmailChannelBackend(self.channel)
        self.addCleanup(transport.close)
        class RequestsEmailBackend(AnymailRequestsBackend):
            esp_name = "Test"

        api = RequestsEmailBackend(api_url="https://mail.example.test/")
        api.open()
        self.addCleanup(api.close)
        url = "https://mail.example.test/send"
        cases = (
            (ConnectTimeout("Connect timed out."), "queued"),
            (RequestsConnectionError(MaxRetryError(None, url, NewConnectionError(None, "Connect refused."))), "queued"),
            (RequestsConnectionError(MaxRetryError(None, url, NameResolutionError(
                "mail.example.test", None, gaierror("DNS failed."),
            ))), "queued"),
            (ReadTimeout("Response lost."), "uncertain"),
            (RequestsConnectionError(ProtocolError("Connection reset after sending.")), "uncertain"),
            (RequestsConnectionError("Connection phase unknown."), "uncertain"),
        )
        payload = SimpleNamespace(get_request_params=lambda api_url: {"method": "POST", "url": url})
        for index, (request_error, expected) in enumerate(cases):
            api.open()
            envelope = SimpleNamespace(from_email="sender@example.test", to=["reader@example.test"], cc=[], bcc=[])
            envelope.send = lambda: api.post_to_esp(payload, envelope)
            with self.subTest(error=type(request_error).__name__), patch.object(
                api.session, "request", side_effect=request_error,
            ), patch.object(transport, "email_message", return_value=envelope), patch(
                "angee.messaging.email.get_connection", return_value=api,
            ), system_context(reason="real Anymail request exception settlement"):
                # post_to_esp, rather than a fabricated wrapper, raises Anymail's
                # real dynamically mixed exception with the requests cause.
                api.open()
                row = self.outbound(external_id=f"http-email-{index}")
                self.backend.deliver.side_effect = transport.deliver
                row.deliver()
                self.run_delivery(row)
                row.refresh_from_db()
                self.assertEqual(row.status, expected)
                self.assertNotIn("mail.example.test", row.local_metadata["delivery_error"])

    def test_admission_and_creation_replay_run_under_the_actor_outside_elevation(self):
        comment = self.comment()
        seen = []

        def admit(parent):
            self.assertFalse(is_sudo())
            self.assertTrue(parent.has_access("read"))
            seen.append(parent.pk)

        with system_context(reason="trusted caller with explicit reply actor"):
            first = Message.objects.compose_reply(comment, body="Reply", sender=self.feed.handle, actor=self.agent,
                                                   idempotency_key="admitted", admit=admit)
            replay = Message.objects.compose_reply(comment, body="Reply", sender=self.feed.handle, actor=self.agent,
                                                    idempotency_key="admitted", admit=admit)
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(seen, [comment.pk])

    def test_elevated_reply_caller_cannot_bypass_the_explicit_actors_permissions(self):
        comment = self.comment()
        with system_context(reason="unprivileged reply actor"):
            stranger = get_user_model().objects.create_user(username="unprivileged-replier")
            with self.assertRaises(PermissionDenied):
                Message.objects.compose_reply(comment, body="Reply", sender=self.feed.handle, actor=stranger,
                                              idempotency_key="denied")
            self.assertFalse(changeset_model()._base_manager.filter(key="denied").exists())

    def test_failed_discovery_receiver_is_repaired_before_classifying_the_next_record(self):
        seen = []

        def observe(sender, instance, **kwargs):
            seen.append(instance.external_id)

        message_ingested.connect(observe)
        self.addCleanup(message_ingested.disconnect, observe)
        horizon = timezone.now() - timedelta(hours=1)
        with system_context(reason="lost discovery receiver fixture"):
            self.feed.live_since = None
            self.feed.discovery_completed_at = horizon
            self.feed.save(update_fields=("live_since", "discovery_completed_at", "updated_at"))
            with patch.object(Feed, "discovery_finished", side_effect=RuntimeError("Receiver failed")):
                with transaction.atomic():
                    transaction.on_commit(lambda: discovery_finished.send(sender=Feed, instance=self.feed), robust=True)
            self.feed.refresh_from_db()
            self.assertIsNone(self.feed.live_since)
            backend = FeedBackend(self.feed)
            backend.apply_record(None, self.post("backfilled", sent_at=horizon - timedelta(seconds=1)))
            self.assertEqual(self.feed.live_since, horizon)
            backend.apply_record(None, self.post("after-discovery", sent_at=horizon + timedelta(seconds=1)))
        self.assertEqual(seen, ["after-discovery"])

    def test_operator_moderation_with_the_same_display_reason_is_not_provider_moderation(self):
        with system_context(reason="moderation provenance fixture"):
            backend = FeedBackend(self.feed)
            backend.apply_record(None, self.post("operator-hidden"))
            row = Message.objects.with_external_ids(["operator-hidden"]).get()
            row.trash(reason="Hidden by the source.")
            backend.apply_record(None, self.post("operator-hidden"))
            row.refresh_from_db()
            self.assertTrue(row.is_trashed)
            self.assertNotIn("provider_hidden", row.local_metadata)

    def test_console_action_bound_is_registered_and_message_list_reads_do_not_grow_per_row(self):
        self.comment()
        schemas = GraphQLSchemas.from_discovery()
        schema = schemas.build("console")
        context = SimpleNamespace(request=SimpleNamespace(user=self.agent))
        with actor_context(self.agent):
            limit = schema.execute_sync("{ action_selection_limit }", context_value=context)
            self.assertIsNone(limit.errors, limit.errors)
            self.assertEqual(limit.data["action_selection_limit"], MAX_ACTION_SELECTION)
            document = "{ messages { id preview } }"
            schema.execute_sync(document, context_value=context)  # Warm schema/permission initialization.
            with CaptureQueriesContext(connection) as small:
                result = schema.execute_sync(document, context_value=context)
            self.assertIsNone(result.errors, result.errors)
        with system_context(reason="larger message list fixture"):
            land_posts(self.feed, [self.post(f"more-{index}", parent="root") for index in range(8)],
                       owner_id=self.operator.pk)
        with actor_context(self.agent), CaptureQueriesContext(connection) as large:
            result = schema.execute_sync(document, context_value=context)
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(len(result.data["messages"]), 10)
        self.assertEqual(len(small), len(large))
        self.assertNotIn("body_text", schemas.graphql_schema("console").get_type("MessageType").fields)

    def test_replier_grant_alone_includes_comment_read(self):
        comment = self.comment()
        with system_context(reason="narrow replier fixture"):
            replier = get_user_model().objects.create_user(username="only-replier")
        self.channel.with_actor(self.operator).grant_record_access("replier", replier)
        with actor_context(replier):
            self.assertTrue(self.channel.with_actor(replier).has_access("read"))
            self.assertTrue(comment.with_actor(replier).has_access("read"))
            self.assertEqual(comment.reply_to_comment(body="Readable context", actor=replier).status, "draft")


class FeedDeliveryContracts(DeliveryFixtures):
    """One shared delivery mechanism; providers supply only publish and bounded lookup."""

    def queued_reply(self):
        comment = self.comment()
        with actor_context(self.agent):
            row = comment.reply_to_comment(body="Thanks!", actor=self.agent)
        with actor_context(self.operator):
            Message.objects.send_held_drafts(self.selection(row), actor=self.operator)
        return row

    def use_provider(self):
        provider = FakeFeedBackend(self.feed)
        self.addCleanup(provider.close)
        self.backend.deliver.side_effect = provider.deliver
        self.backend.reconciles_delivery = provider.reconciles_delivery
        self.backend.delivery_visibility_delay = provider.delivery_visibility_delay
        return provider

    def test_feed_capability_is_read_from_the_class_without_constructing_a_backend(self):
        self.assertFalse(FeedBackend.reconciles_delivery())
        self.assertTrue(FakeFeedBackend.reconciles_delivery())
        with system_context(reason="class-only transport capability"), patch.object(
            Feed.feed_backend_class.field, "resolve_class", return_value=FakeFeedBackend,
        ), patch.object(FakeFeedBackend, "__init__", side_effect=AssertionError("Constructed a backend")):
            self.assertTrue(self.original_channel_capability(self.channel))
            self.assertEqual(self.original_channel_visibility_delay(self.channel), timedelta())

    def test_first_reconciliation_waits_for_visibility_margin_without_lookup_or_quota_debit(self):
        row = self.queued_reply()
        provider = self.use_provider()
        self.assertEqual(FeedBackend.delivery_visibility_delay, timedelta(minutes=3))
        provider.delivery_visibility_delay = FeedBackend.delivery_visibility_delay
        self.backend.delivery_visibility_delay = provider.delivery_visibility_delay
        at = timezone.now()
        visible_at = at + provider.delivery_visibility_delay
        with system_context(reason="provider read-after-write margin"), patch(
            "django.utils.timezone.now", return_value=at,
        ):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            provider.deliver(claimed)
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Lost acknowledgement."))
            row.refresh_from_db()
            self.assertEqual(row.scheduled_at, visible_at)
            self.assertIsNone(row.delivery_lease_until)
            self.assertEqual(self.run_delivery(row)["status"], "uncertain")
            self.assertEqual(Message.objects.release_held_messages(), 0)
            with self.assertRaises(DeliveryUncertain):
                provider.deliver(row)
            row.refresh_from_db()
            self.assertEqual(row.delivery_reconcile_attempts, 0)
            self.assertEqual(provider.current_quota_period().quota_used, provider.publish_reply_cost)
        self.assertEqual(provider.lookups, [])
        with system_context(reason="reply edge visible"), patch("django.utils.timezone.now", return_value=visible_at):
            self.assertTrue(self.run_delivery(row)["delivered"])
        self.assertEqual(len(provider.lookups), 1)
        self.assertEqual(provider.posts, 1)

    def test_invalid_anchor_is_refused_before_publication_and_any_quota_debit(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="pre-send anchor refusal"):
            Message.objects.filter(pk=row.parent_id).update(external_id="")
            self.assertEqual(self.run_delivery(row)["status"], "failed")
            row.refresh_from_db()
            self.assertFalse(row.delivery_possibly_accepted)
            self.assertEqual(apps.get_model("posts", "Quota").objects.count(), 0)
        self.assertEqual(provider.posts, 0)
        self.assertEqual(provider.lookups, [])

    def test_invalid_anchor_after_possible_acceptance_preserves_uncertainty_without_spending_quota(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="post-send anchor loss"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            provider.deliver(claimed)
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Lost acknowledgement."))
            Message.objects.filter(pk=row.parent_id).update(external_id="")
            self.assertEqual(self.run_delivery(row)["status"], "uncertain")
            self.assertEqual(provider.current_quota_period().quota_used, provider.publish_reply_cost)
        self.assertEqual(provider.posts, 1)
        self.assertEqual(provider.lookups, [])

    def test_current_quota_period_opens_after_reset_without_prior_debit(self):
        provider = self.use_provider()
        before = datetime(2026, 1, 1, 23, 59, 59, tzinfo=UTC)
        after = before + timedelta(seconds=1)
        first = provider.consume_quota(1, now=before)
        current = provider.current_quota_period(now=after)
        self.assertNotEqual(first.pk, current.pk)
        self.assertEqual(current.period_start, after)
        self.assertEqual(current.period_end, after + timedelta(days=1))
        self.assertEqual(current.quota_used, 0)
        self.assertEqual(provider.current_quota_period(now=after).pk, current.pk)

    def test_reobservation_refusal_spends_neither_cycle_allowance_nor_daily_quota(self):
        provider = self.use_provider()
        self.feed.config = {"quota_limit": 5, "reply_reserve": 2, "reobserve_units_per_cycle": 2}
        with system_context(reason="reobservation quota boundaries"):
            self.assertFalse(provider.consume_reobserve_quota(3))
            self.assertEqual(provider.current_quota_period().quota_used, 0)
            provider.consume_quota(3)
            self.assertFalse(provider.consume_reobserve_quota(1))
            self.assertEqual(provider.current_quota_period().quota_used, 3)
            self.feed.config["quota_limit"] = 7
            self.assertTrue(provider.consume_reobserve_quota(2))
            self.assertFalse(provider.consume_reobserve_quota(1))
            self.assertEqual(provider.current_quota_period().quota_used, 5)
            self.feed.config["reobserve_units_per_cycle"] = 0
            self.assertFalse(FakeFeedBackend(self.feed).consume_reobserve_quota(1))
            for units in (0, -1):
                with self.subTest(units=units), self.assertRaises(ValueError):
                    provider.consume_reobserve_quota(units)

    def test_first_send_publishes_without_matching_identical_text_on_another_comment(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.published.append({"id": "another-reply", "parent": -1, "author": self.feed.handle_id,
                                   "created_at": timezone.now(), "body": "Thanks!"})
        with system_context(reason="first provider publication"):
            self.assertTrue(self.run_delivery(row)["delivered"])
            row.refresh_from_db()
        self.assertEqual(provider.posts, 1)
        self.assertEqual(provider.lookups, [])
        self.assertEqual(row.external_id, "provider-1")

    def test_lost_claim_reconciles_only_the_same_parent_since_the_first_claim_without_second_post(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="provider acceptance before crash"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            self.assertIsNotNone(claimed.delivery_started_at)
            accepted = provider.deliver(claimed)
            self.assertTrue(accepted.accepted)
            # A duplicate task with a live row claim cannot make another provider call.
            self.assertIsNone(Message.objects.claim_delivery(row.pk, token=row.external_id))
            provider.published.extend([
                {"id": "other-parent", "parent": -1, "author": self.feed.handle_id,
                 "created_at": timezone.now(), "body": "Thanks!"},
                {"id": "old-reply", "parent": row.parent_id, "author": self.feed.handle_id,
                 "created_at": claimed.delivery_started_at - timedelta(minutes=10), "body": "Thanks!"},
            ])
            Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
            self.assertEqual(Message.objects.release_held_messages(), 1)
            row.refresh_from_db()
            self.assertEqual(row.status, "uncertain")
            self.assertTrue(self.run_delivery(row)["delivered"])
            row.refresh_from_db()
        self.assertEqual(provider.posts, 1)
        self.assertEqual(provider.lookups, [(row.top_level_comment().pk,
                                             claimed.delivery_started_at.replace(microsecond=0)
                                             - RECONCILIATION_SKEW_MARGIN)])
        self.assertEqual(row.external_id, accepted.provider_id)
        self.assertEqual(row.status, "sent")

    def test_reply_published_in_the_claim_second_is_found_without_reposting(self):
        row = self.queued_reply()
        provider = self.use_provider()
        claim_at = timezone.now().replace(microsecond=987654)
        with system_context(reason="same-second provider clock"), patch(
            "django.utils.timezone.now", return_value=claim_at,
        ):
            claim = Message.objects.claim_delivery(row.pk, token=row.external_id)
            provider.deliver(claim)
            self.assertEqual(provider.published[0]["created_at"], claim_at.replace(microsecond=0))
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claim.delivery_claim,
                                            error=DeliveryUncertain("Lost acknowledgement."))
            self.assertTrue(self.run_delivery(row)["delivered"])
        self.assertEqual(provider.posts, 1)
        self.assertEqual(provider.lookups[0][1], claim_at.replace(microsecond=0) - RECONCILIATION_SKEW_MARGIN)
        with system_context(reason="both delivery hooks debit their declared costs"):
            period = apps.get_model("posts", "Quota").objects.get(scope=provider.quota_scope())
            self.assertEqual(period.quota_used, provider.publish_reply_cost + provider.find_published_reply_cost)

    def test_identical_replies_to_nested_comments_under_one_anchor_are_ambiguous(self):
        self.comment()
        provider = self.use_provider()
        with system_context(reason="nested comments under a shared anchor"):
            first_comment, second_comment = land_posts(
                self.feed, [self.post("nested-first", parent="comment"), self.post("nested-other", parent="comment")],
                owner_id=self.operator.pk,
            )
        with actor_context(self.agent):
            first = first_comment.reply_to_comment(body="Thanks!", actor=self.agent)
            second = second_comment.reply_to_comment(body="Thanks!", actor=self.agent)
        with actor_context(self.operator):
            Message.objects.send_held_drafts(self.selection(first), actor=self.operator)
        with system_context(reason="ambiguous recovery"):
            self.assertEqual(first.top_level_comment().pk, second.top_level_comment().pk)
            claim = Message.objects.claim_delivery(first.pk, token=first.external_id)
            provider.deliver(claim)
            Message.objects.record_delivery(first.pk, token=first.external_id, claim=claim.delivery_claim,
                                            error=DeliveryUncertain("Lost acknowledgement."))
            self.assertEqual(self.run_delivery(first)["status"], "uncertain")
            first.refresh_from_db()
            self.assertTrue(first.delivery_possibly_accepted)
        self.assertEqual(provider.posts, 1)
        self.assertEqual(provider.lookups, [])

    def test_reconciliation_backs_off_then_becomes_operator_only_without_revision_churn(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.publish_error = DeliveryUncertain("Lost acknowledgement.")
        with system_context(reason="bounded reconciliation fixture"):
            self.assertEqual(self.run_delivery(row)["status"], "uncertain")
            provider.lookup_error = IntegrationError(retry_after=timedelta(seconds=1))
            row.refresh_from_db()
            revision = row.revision
            for attempt in range(1, delivery.MAX_RECONCILE_ATTEMPTS + 1):
                at = timezone.now()
                Message.objects.filter(pk=row.pk).update(delivery_lease_until=at, scheduled_at=at)
                with patch("django.utils.timezone.now", return_value=at):
                    self.assertEqual(self.run_delivery(row)["status"], "uncertain")
                row.refresh_from_db()
                self.assertEqual(row.revision, revision)
                self.assertEqual(row.delivery_reconcile_attempts, attempt)
                self.assertEqual(row.status, "uncertain")
                if attempt < delivery.MAX_RECONCILE_ATTEMPTS:
                    self.assertEqual(
                        row.scheduled_at, at + delivery.RECONCILE_BACKOFF * 2 ** (attempt - 1),
                    )
                    self.assertEqual(Message.objects.release_held_messages(now=at), 0)
            self.assertTrue(row.delivery_operator_only)
            with patch.object(Channel, "backend", property(
                lambda channel: self.fail("Sweep opened an operator-only backend"),
            )):
                self.assertEqual(Message.objects.release_held_messages(now=at + timedelta(days=1)), 0)
        self.assertEqual(provider.posts, 1)

    def test_definite_refusal_retry_does_not_lookup_even_when_lookup_would_fail(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.lookup_error = IntegrationError("Lookup unavailable.")
        with system_context(reason="definite non-acceptance retry"), patch.object(
            provider, "publish_reply",
            side_effect=[IntegrationError(retry_after=timedelta(seconds=1)), "provider-retry"],
        ) as publish:
            self.assertEqual(self.run_delivery(row)["status"], "retry")
            row.refresh_from_db()
            self.assertFalse(row.delivery_possibly_accepted)
            Message.objects.filter(pk=row.pk).update(scheduled_at=None)
            self.assertTrue(self.run_delivery(row)["delivered"])
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(provider.lookups, [])

    def test_delivery_debits_declared_costs_from_the_reserved_reply_budget_once(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.publish_reply_cost = 2
        with system_context(reason="reply reserve delivery"):
            self.feed.config = {"quota_limit": 5, "reply_reserve": 2}
            self.feed.save(update_fields=("config", "updated_at"))
            provider.consume_quota(3)
            with self.assertRaises(IntegrationError):
                provider.consume_quota(1)
            quota_manager = apps.get_model("posts", "Quota").objects
            original_open = quota_manager.open_period
            with patch.object(type(quota_manager), "open_period", autospec=True,
                              side_effect=lambda manager, **kwargs: original_open(**kwargs)) as opened:
                self.assertTrue(self.run_delivery(row)["delivered"])
            self.assertEqual(opened.call_count, 1)
            period = quota_manager.get(scope=provider.quota_scope())
            self.assertEqual(period.quota_used, 5)
        self.assertEqual(provider.posts, 1)

    def test_post_send_lookup_failure_remains_uncertain_even_when_the_lookup_error_is_transient(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.publish_error = DeliveryUncertain("Lost acknowledgement.")
        provider.lookup_error = IntegrationError(retry_after=timedelta(seconds=30))
        with system_context(reason="ambiguous publish and failed reconciliation"):
            result = self.run_delivery(row)
            row.refresh_from_db()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(row.status, "uncertain")
        self.assertIsNone(row.delivery_lease_until)
        self.assertIsNotNone(row.scheduled_at)
        self.assertEqual(provider.posts, 1)

    def test_operator_only_uncertain_rows_do_not_starve_the_reconciliation_batch(self):
        with system_context(reason="operator-only uncertainty fixture"):
            other = Channel.objects.create(vendor=self.feed.vendor, backend_class="manual", owner=self.operator)
            first = self.outbound(channel=other, external_id="operator-only")
            first.deliver()
            claimed = Message.objects.claim_delivery(first.pk, token=first.external_id)
            Message.objects.record_delivery(first.pk, token=first.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
        provider = self.use_provider()
        operator_transport = SimpleNamespace(reconciles_delivery=lambda: False, close=Mock())
        self.enterContext(patch.object(Channel, "backend", property(
            lambda channel: operator_transport if channel.pk == other.pk else self.backend,
        )))
        row = self.queued_reply()
        with system_context(reason="bounded reconciliation behind operator decision"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            provider.deliver(claimed)
            Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
            self.assertEqual(Message.objects.release_held_messages(limit=1), 1)
            self.assertTrue(self.run_delivery(row)["delivered"])
            first.refresh_from_db()
            self.assertEqual(first.status, "uncertain")
        self.assertEqual(provider.posts, 1)

    def test_reconciliation_lock_contention_preserves_uncertainty(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="uncertain reconciliation contention"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            provider.deliver(claimed)
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
            self.backend.delivery_lock = lambda: nullcontext(False)
            self.assertEqual(self.run_delivery(row)["status"], "uncertain")
            row.refresh_from_db()
            self.assertEqual(row.status, "uncertain")
            self.assertIsNotNone(row.delivery_claim)
            self.assertIsNone(row.delivery_lease_until)
            self.assertIsNotNone(row.scheduled_at)
        self.assertEqual(provider.posts, 1)
        self.assertEqual(provider.lookups, [])

    def test_uncertain_reply_with_lost_parent_context_does_not_fail_or_republish(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="uncertain reply context loss"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            provider.deliver(claimed)
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
            Message.objects.filter(pk=row.pk).update(parent=None)
            self.assertEqual(self.run_delivery(row)["status"], "uncertain")
        self.assertEqual(provider.posts, 1)
        self.assertEqual(provider.lookups, [])

    def test_post_send_ambiguity_is_settled_before_lookup_without_a_second_publish(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.publish_error = DeliveryUncertain("Lost acknowledgement.")
        with system_context(reason="immediate provider reconciliation"):
            self.assertEqual(self.run_delivery(row)["status"], "uncertain")
            self.assertEqual(provider.lookups, [])
            self.assertTrue(self.run_delivery(row)["delivered"])
            row.refresh_from_db()
        self.assertEqual(provider.posts, 1)
        self.assertEqual(row.external_id, "provider-1")

    def test_quota_reserve_is_shared_per_scope_and_uses_current_configuration(self):
        with system_context(reason="shared quota fixture"):
            self.feed.config = {"quota_limit": 5, "reply_reserve": 2}
            self.feed.save(update_fields=("config", "updated_at"))
            second = Feed.objects.create(vendor=self.feed.vendor, owner=self.operator, display_name="Other feed",
                                         external_id="other", backend_class="feed", feed_backend_class="manual",
                                         config={"quota_limit": 5, "reply_reserve": 2})
            first_provider = FakeFeedBackend(self.feed)
            second_provider = FakeFeedBackend(second)
            self.addCleanup(first_provider.close)
            self.addCleanup(second_provider.close)
            first_provider.consume_quota(3)
            with self.assertRaises(IntegrationError) as refused:
                second_provider.consume_quota(1)
            self.assertTrue(refused.exception.transient)
            second_provider.consume_quota(2, delivery=True)
            quota = apps.get_model("posts", "Quota").objects.get(scope=first_provider.quota_scope())
            self.assertEqual(quota.quota_used, 5)
            second.config = {"quota_limit": 8, "reply_reserve": 2}
            second.save(update_fields=("config", "updated_at"))
            second_provider.consume_quota(1)
            quota.refresh_from_db()
            self.assertEqual(quota.quota_used, 6)
            with self.assertRaises(IntegrationError):
                first_provider.consume_quota(1, delivery=True)  # This feed still checks its own limit of 5.
            quota.refresh_from_db()
            self.assertEqual(quota.quota_used, 6)
            second_provider.scope = "fake:project:independent"
            second_provider.consume_quota(3)
            self.assertEqual(apps.get_model("posts", "Quota").objects.count(), 2)

    def test_quota_period_uses_provider_midnight_across_dst(self):
        provider = FakeFeedBackend(self.feed)
        self.addCleanup(provider.close)
        provider.quota_reset_timezone = "America/New_York"
        period = provider.consume_quota(1, now=datetime(2026, 3, 8, 6, 30, tzinfo=UTC))
        with system_context(reason="quota timezone assertion"):
            period.refresh_from_db()
        self.assertEqual(period.period_start, datetime(2026, 3, 8, 5, tzinfo=UTC))
        self.assertEqual(period.period_end, datetime(2026, 3, 9, 4, tzinfo=UTC))
        self.assertEqual(period.period_end - period.period_start, timedelta(hours=23))

    def test_feed_native_transient_refusal_is_retryable_before_any_effect(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="definite provider non-acceptance"), patch.object(
            provider, "publish_reply", side_effect=IntegrationError(retry_after=timedelta(seconds=30)),
        ):
            self.assertEqual(self.run_delivery(row)["status"], "retry")
            row.refresh_from_db()
        self.assertEqual(row.status, "queued")
        self.assertEqual(provider.posts, 0)
        self.assertEqual(provider.lookups, [])

    def test_quota_authorization_anchor_is_required_and_protected(self):
        with system_context(reason="shared quota anchor lifecycle"):
            second = Feed.objects.create(vendor=self.feed.vendor, owner=self.operator, display_name="Remaining feed",
                                         external_id="remaining", backend_class="feed", feed_backend_class="manual")
            first_provider = FakeFeedBackend(self.feed)
            second_provider = FakeFeedBackend(second)
            self.addCleanup(first_provider.close)
            self.addCleanup(second_provider.close)
            period = first_provider.consume_quota(3)
            with self.assertRaises(ProtectedError):
                self.feed.delete()
            period.refresh_from_db()
            same_period = second_provider.consume_quota(2)
            same_period.refresh_from_db()
        self.assertEqual(period.integration_id, self.feed.pk)
        self.assertEqual(same_period.pk, period.pk)
        self.assertEqual(same_period.quota_used, 5)

    def test_retry_keeps_possible_acceptance_after_backend_construction_failure(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.publish_error = DeliveryUncertain("Acknowledgement lost.")
        with system_context(reason="retry across transport construction failure"):
            self.assertEqual(self.run_delivery(row)["status"], "uncertain")
            row.refresh_from_db()
        with actor_context(self.operator):
            row = row.retry_delivery(actor=self.operator, expected_revision=row.revision)

        def broken_backend(channel):
            raise IntegrationError("Transport unavailable.")

        with patch.object(Channel, "backend", property(broken_backend)):
            self.assertEqual(self.run_delivery(row)["status"], "uncertain")
        with system_context(reason="prior effect still reserved"):
            row.refresh_from_db()
            self.assertTrue(row.delivery_needs_reconciliation)
            self.assertTrue(row.parent.reply_state())
        with actor_context(self.operator):
            row = row.retry_delivery(actor=self.operator, expected_revision=row.revision)
        self.assertTrue(self.run_delivery(row)["delivered"])
        self.assertEqual(provider.posts, 1)
        self.assertEqual(len(provider.lookups), 1)

    def test_trashed_claim_stays_answered_and_reconciles_after_its_live_lease(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="trash a claimed effect"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            provider.deliver(claimed)
            row.refresh_from_db()
            row = row.with_actor(self.operator)
            row.trash(reason="Removed locally during publication.")
            self.assertEqual(row.status, "uncertain")
            self.assertEqual(row.delivery_lease_until, claimed.delivery_lease_until)
            self.assertTrue(row.parent.reply_state())
            Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
            self.assertEqual(Message.objects.release_held_messages(), 1)
            self.assertTrue(self.run_delivery(row)["delivered"])
            row.refresh_from_db()
            self.assertTrue(row.is_trashed)
            self.assertTrue(row.parent.reply_state())
        self.assertEqual(provider.posts, 1)
        self.assertEqual(len(provider.lookups), 1)

    def test_trashed_uncertainty_can_be_acknowledged_or_explicitly_restored_for_retry(self):
        self.comment()
        provider = self.use_provider()
        for retry in (False, True):
            with self.subTest(retry=retry), system_context(reason="trashed operator fixture"):
                comment = land_posts(self.feed, [self.post(f"operator-{retry}", parent="root")],
                                     owner_id=self.operator.pk)[0]
            with actor_context(self.agent):
                row = comment.reply_to_comment(body=f"Reply {retry}", actor=self.agent)
            with actor_context(self.operator):
                Message.objects.send_held_drafts(self.selection(row), actor=self.operator)
            with system_context(reason="trash and expire committed effect"):
                claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
                provider.deliver(claimed)
                row.refresh_from_db()
                row = row.with_actor(self.operator)
                row.trash()
                Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
                row.refresh_from_db()
                self.assertTrue(comment.reply_state())
            with actor_context(self.operator):
                if retry:
                    row = row.retry_delivery(actor=self.operator, expected_revision=row.revision)
                    self.assertFalse(row.is_trashed)
                    self.assertTrue(self.run_delivery(row)["delivered"])
                else:
                    row = row.acknowledge_delivery(actor=self.operator, expected_revision=row.revision)
                    self.assertEqual(row.status, "sent")
            with system_context(reason="answer survives operator settlement"):
                self.assertTrue(comment.reply_state())
        self.assertEqual(provider.posts, 2)
        self.assertEqual(len(provider.lookups), 1)

    def test_ambiguity_has_one_query_for_many_candidates_and_skips_invalid_chains(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.publish_error = DeliveryUncertain("Acknowledgement lost.")
        self.run_delivery(row)
        for index in range(35):
            with actor_context(self.operator):
                Message.objects.compose_reply(parent=row.parent, body=f"Other {index}",
                                              sender=self.feed.handle, actor=self.operator)
        with system_context(reason="unrelated broken chain"):
            broken = Message.objects.create(channel_id=self.feed.pk, thread_id=row.thread_id,
                                            external_id="unrooted", direction="inbound", platform="youtube")
        with actor_context(self.operator):
            Message.objects.compose_reply(parent=broken, body="Thanks!", sender=self.feed.handle, actor=self.operator)
        since = timezone.now() - timedelta(days=3)
        with CaptureQueriesContext(connection) as queries:
            self.assertFalse(row.has_ambiguous_delivery_reply(since=since))
        # One comparison plus the native system-context audit setup on a cold
        # context. Neither cost grows with the number of candidate replies.
        self.assertLessEqual(len(queries), 2, queries.captured_queries)
        self.assertEqual(sum("WITH RECURSIVE candidates" in query["sql"] for query in queries), 1)
        self.assertTrue(self.run_delivery(row)["delivered"])
        self.assertEqual(provider.posts, 1)

    def test_sql_plain_body_matches_the_tree_reader_and_detects_multipart_ambiguity(self):
        row = self.queued_reply()
        with actor_context(self.operator):
            other = Message.objects.compose_reply(parent=row.parent, body="Other",
                                                  sender=self.feed.handle, actor=self.operator)
        with system_context(reason="nested plain-body projection"):
            Part = apps.get_model("messaging", "Part")
            for message in (row, other):
                message.parts.all().delete()
                Message.objects._build_parts(message, ParsedPart(type="multipart/mixed", children=(
                    ParsedPart(text="One"), ParsedPart(type="multipart/alternative", children=(
                        ParsedPart(text="Two"), ParsedPart(type="text/html", text="<p>Two</p>"),
                    )),
                )), parent=None, position=0, created_by_id=self.operator.pk)
                sql, params = Part.objects.plain_body_sql("%s", connection=connection)
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT {sql}", [message.pk, *params])
                    self.assertEqual(cursor.fetchone()[0], message.body_text())
            self.assertTrue(row.has_ambiguous_delivery_reply(since=timezone.now() - timedelta(days=1)))

    def test_sql_ambiguity_window_includes_the_exact_creation_or_claim_boundary(self):
        row = self.queued_reply()
        with actor_context(self.operator):
            other = Message.objects.compose_reply(parent=row.parent, body="Thanks!",
                                                  sender=self.feed.handle, actor=self.operator)
        since = timezone.now() - timedelta(minutes=1)
        with system_context(reason="native datetime preparation for the SQL window"):
            Message.objects.filter(pk=other.pk).update(created_at=since)
            self.assertTrue(row.has_ambiguous_delivery_reply(since=since))
            Message.objects.filter(pk=other.pk).update(created_at=since - timedelta(microseconds=1))
            self.assertFalse(row.has_ambiguous_delivery_reply(since=since))
            Message.objects.filter(pk=other.pk).update(delivery_started_at=since)
            self.assertTrue(row.has_ambiguous_delivery_reply(since=since))

    def test_authoritative_absence_clears_the_marker_before_a_refused_fresh_publish(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="authoritative absence fixture"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
        provider.publish_reply = Mock(side_effect=IntegrationError("Not sent.", transient=True))
        self.assertEqual(self.run_delivery(row)["status"], "retry")
        with system_context(reason="absence releases possible effect"):
            row.refresh_from_db()
            self.assertFalse(row.delivery_needs_reconciliation)
            Message.objects.filter(pk=row.pk).update(scheduled_at=None)
        self.assertEqual(self.run_delivery(row)["status"], "retry")
        self.assertEqual(len(provider.lookups), 1)

    def test_worker_loss_after_confirmed_absence_expires_into_uncertainty_and_never_reposts(self):
        row = self.queued_reply()
        provider = self.use_provider()

        class WorkerLost(Exception):
            pass

        with system_context(reason="lost acknowledgement, then a lost fresh attempt"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id, reconcile=True)
            provider.publish_error = WorkerLost()
            with self.assertRaises(WorkerLost):
                provider.deliver(claimed)  # The fresh POST lands; its settlement never runs.
            row.refresh_from_db()
            self.assertEqual((row.status, row.delivery_possibly_accepted), ("queued", False))
            self.assertEqual(row.delivery_claim, claimed.delivery_claim)
            Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
            Message.objects.release_held_messages()
            row.refresh_from_db()
            self.assertEqual((row.status, row.delivery_possibly_accepted), ("uncertain", True))
        provider.publish_error = None
        with actor_context(self.operator):
            row = row.retry_delivery(actor=self.operator, expected_revision=row.revision)
        self.assertTrue(self.run_delivery(row)["delivered"])
        with system_context(reason="the lost fresh POST is the settled identity"):
            row.refresh_from_db()
            self.assertEqual((row.status, row.external_id), ("sent", "provider-1"))
        self.assertEqual(provider.posts, 1)
        self.assertEqual(len(provider.lookups), 2)

    def test_collision_settles_a_reply_the_operator_already_trashed(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="stream ingests a reply the operator trashed"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            accepted = provider.deliver(claimed)
            landed = land_posts(self.feed, [ParsedPost(ParsedMessage(
                external_id=accepted.provider_id, platform="youtube", direction="outbound",
                sender=ParsedHandle(platform="youtube", value="publisher"), in_reply_to="comment",
                sent_at=timezone.now(), body=ParsedPart(text="Thanks!"),
            ))], owner_id=self.operator.pk)[0]
            row.refresh_from_db()
            row.with_actor(self.operator).trash(reason="Removed locally during publication.")
            Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
            self.assertEqual(Message.objects.release_held_messages(), 1)
            self.assertTrue(self.run_delivery(row)["delivered"])
            row.refresh_from_db()
            self.assertTrue(row.is_trashed)
            self.assertEqual(row.trash_reason, "Removed locally during publication.")
            self.assertEqual((row.status, row.local_metadata["delivery_conflict"]), ("sent", accepted.provider_id))
            self.assertFalse(Message.objects.get(pk=landed.pk).is_trashed)
        self.assertEqual(provider.posts, 1)

    def test_trashed_sent_reply_keeps_an_identical_sibling_reply_ambiguous(self):
        self.comment()
        provider = self.use_provider()
        with system_context(reason="nested comments under a shared anchor"):
            first_comment, second_comment = land_posts(
                self.feed, [self.post("nested-first", parent="comment"), self.post("nested-other", parent="comment")],
                owner_id=self.operator.pk,
            )
        with actor_context(self.agent):
            first = first_comment.reply_to_comment(body="Thanks!", actor=self.agent)
            second = second_comment.reply_to_comment(body="Thanks!", actor=self.agent)
        with actor_context(self.operator):
            Message.objects.send_held_drafts(self.selection(first), actor=self.operator)
        self.assertTrue(self.run_delivery(first)["delivered"])
        with system_context(reason="the published reply is retained only as a trashed SENT row"):
            first.refresh_from_db()
            first.with_actor(self.operator).trash(reason="Settled onto the ingested reply.")
            second.refresh_from_db()
        with actor_context(self.operator):
            Message.objects.send_held_drafts(self.selection(second), actor=self.operator)
        with system_context(reason="the second reply's worker is lost before its POST"):
            claimed = Message.objects.claim_delivery(second.pk, token=second.external_id)
            Message.objects.record_delivery(second.pk, token=second.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
            self.assertEqual(self.run_delivery(second)["status"], "uncertain")
            second.refresh_from_db()
            self.assertEqual((second.status, second.is_trashed), ("uncertain", False))
            self.assertNotIn("delivery_conflict", second.local_metadata)
        self.assertEqual(provider.posts, 1)
        self.assertEqual(provider.lookups, [])

    def test_live_reconcile_lease_refuses_operator_verbs_without_losing_provider_identity(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.publish_error = DeliveryUncertain("Acknowledgement lost.")
        self.run_delivery(row)
        with system_context(reason="live reconcile worker"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id, reconcile=True)
            row.refresh_from_db()
        for verb in (row.acknowledge_delivery, row.retry_delivery):
            with actor_context(self.operator), self.assertRaises(DeliveryReconciliationBusy) as refusal:
                verb(actor=self.operator, expected_revision=row.revision)
            self.assertEqual(refusal.exception.code, "delivery-reconciling")
        with system_context(reason="worker keeps settlement fence"):
            outcome = provider.deliver(claimed)
            self.assertEqual(Message.objects.record_delivery(
                row.pk, token=row.external_id, claim=claimed.delivery_claim, outcome=outcome,
            ), "sent")
            row.refresh_from_db()
            self.assertEqual(row.external_id, "provider-1")
        self.assertEqual(provider.posts, 1)

    def test_reconciled_identity_already_ingested_has_one_visible_row_and_one_count(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="stream beats lost-settlement recovery"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            accepted = provider.deliver(claimed)
            landed = land_posts(self.feed, [ParsedPost(ParsedMessage(
                external_id=accepted.provider_id, platform="youtube", direction="outbound",
                sender=ParsedHandle(platform="youtube", value="publisher"), in_reply_to="comment",
                sent_at=timezone.now(), body=ParsedPart(text="Thanks!"),
            ))], owner_id=self.operator.pk)[0]
            Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
            self.assertEqual(Message.objects.release_held_messages(), 1)
            self.assertTrue(self.run_delivery(row)["delivered"])
            row.refresh_from_db()
            self.assertTrue(row.is_trashed)
            self.assertEqual((row.status, row.local_metadata["delivery_conflict"]), ("sent", accepted.provider_id))
            self.assertFalse(Message.objects.get(pk=landed.pk).is_trashed)
            row.thread.refresh_from_db()
            self.assertEqual(row.thread.message_count, 3)  # Original post, comment, provider reply.
            self.assertEqual(Message.objects.filter(thread_id=row.thread_id, is_trashed=False).count(), 3)
        self.assertEqual(provider.posts, 1)

    def test_identical_operator_typed_page_reply_satisfies_the_lookup_contract(self):
        row = self.queued_reply()
        provider = self.use_provider()
        with system_context(reason="manual Page reply in the original claim window"):
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            provider.published.append({"id": "manual-page-reply", "parent": row.parent_id,
                                       "author": self.feed.handle_id, "created_at": timezone.now(), "body": "Thanks!"})
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
            self.assertTrue(self.run_delivery(row)["delivered"])
            row.refresh_from_db()
            self.assertEqual(row.external_id, "manual-page-reply")
        self.assertEqual(provider.posts, 0)

    def test_sweep_leases_pending_reconciliation_without_redispatch_each_minute(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.publish_error = DeliveryUncertain("Acknowledgement lost.")
        self.run_delivery(row)
        with system_context(reason="reconciliation waits in the broker"):
            at = timezone.now()
            self.assertEqual(Message.objects.release_held_messages(now=at), 1)
            dispatched = self.broker.call_count
            self.assertEqual(Message.objects.release_held_messages(now=at + timedelta(minutes=1)), 0)
            self.assertEqual(self.broker.call_count, dispatched)
            with patch("django.utils.timezone.now", return_value=at + timedelta(seconds=301)):
                self.assertEqual(Message.objects.release_held_messages(), 1)
            self.assertEqual(self.broker.call_count, dispatched + 1)
        self.assertTrue(self.run_delivery(row)["delivered"])
        self.assertEqual(provider.posts, 1)

    def test_delivery_routing_pins_the_configured_prefork_queue_even_with_a_threads_queue_route(self):
        # Celery's Settings mapping cannot delete an attribute, so restore it by value.
        self.addCleanup(setattr, celery_app.conf, "task_default_queue", celery_app.conf.task_default_queue)
        celery_app.conf.task_default_queue = "prefork"
        self.queued_reply()
        self.assertEqual(self.broker.call_args.kwargs["queue"], "prefork")
        router = Router(routes=[MapRoute({"messaging.*": {"queue": "whatsapp"}})],
                        queues=celery_app.amqp.queues, app=celery_app)
        route = router.route({"queue": "prefork"}, delivery.DELIVER_MESSAGE_TASK, kwargs={})
        self.assertEqual(route["queue"].name, "prefork")

    def test_lost_reconcile_claims_use_the_same_attempt_cap_as_settled_failures(self):
        row = self.queued_reply()
        provider = self.use_provider()
        provider.publish_error = DeliveryUncertain("Acknowledgement lost.")
        self.run_delivery(row)
        with system_context(reason="repeated recovery worker loss"):
            for attempt in range(1, delivery.MAX_RECONCILE_ATTEMPTS + 1):
                claimed = Message.objects.claim_delivery(row.pk, token=row.external_id, reconcile=True)
                self.assertIsNotNone(claimed)
                self.assertEqual(claimed.delivery_reconcile_attempts, attempt)
                Message.objects.filter(pk=row.pk).update(delivery_lease_until=timezone.now() - timedelta(seconds=1))
            self.assertIsNone(Message.objects.claim_delivery(row.pk, token=row.external_id, reconcile=True))
            row.refresh_from_db()
            self.assertTrue(row.delivery_operator_only)
            self.assertIsNone(row.delivery_lease_until)
            self.assertEqual(Message.objects.release_held_messages(), 0)
        self.assertEqual(provider.posts, 1)
        self.assertEqual(provider.lookups, [])
