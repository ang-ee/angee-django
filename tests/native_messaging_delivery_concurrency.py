"""PostgreSQL delivery interleavings on the actual composed model graph."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db import close_old_connections, connections, transaction
from django.utils import timezone
from rebac import actor_context, system_context

from angee.integrate.locks import bridge_advisory_lock
from tests.native_messaging_delivery import DeliveryFixtures, Message


def independent_connection(call):
    """Keep each competing verb on its own native database session."""

    close_old_connections()
    try:
        return call()
    finally:
        connections.close_all()


class DeliveryConcurrency(DeliveryFixtures):
    def test_sweep_skips_a_claimed_row_and_enqueues_each_due_message_once(self):
        first = self.outbound(scheduled_at=timezone.now() - timedelta(seconds=1))
        with system_context(reason="due race fixture"):
            second = Message.objects.create(channel_id=self.feed.pk, direction="outbound", status="draft",
                                            external_id="second", scheduled_at=first.scheduled_at)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic(), system_context(reason="sweep claim race"):
                Message.objects.select_for_update().get(pk=first.pk)
                contender = pool.submit(independent_connection, Message.objects.release_held_messages)
                self.assertEqual(contender.result(timeout=10), 1)
                self.assertEqual(self.broker.call_count, 1)
                self.assertEqual(Message.objects.release_held_messages(), 1)
                self.assertEqual(self.broker.call_count, 1)
            self.assertEqual(self.broker.call_count, 2)
        with system_context(reason="settled sweep check"):
            first.refresh_from_db()
            second.refresh_from_db()
            self.assertEqual((first.status, second.status), ("queued", "queued"))
            self.assertEqual(Message.objects.release_held_messages(), 0)

    def test_feed_sync_advisory_lock_defers_delivery_without_a_provider_attempt(self):
        row = self.outbound()
        self.backend.delivery_lock = lambda: bridge_advisory_lock(self.feed)
        with system_context(reason="delivery lock fixture"):
            row.deliver()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with bridge_advisory_lock(self.feed) as acquired:
                self.assertTrue(acquired)
                contender = pool.submit(independent_connection, lambda: self.run_delivery(row))
                self.assertEqual(contender.result(timeout=10)["status"], "retry")
                self.backend.deliver.assert_not_called()
        with system_context(reason="contention settlement check"):
            row.refresh_from_db()
            self.assertNotIn("delivery_attempts", row.local_metadata)
            self.assertGreater(row.scheduled_at, timezone.now())
            self.assertGreater(row.delivery_lease_until, row.scheduled_at)

    def test_send_and_discard_recheck_the_same_selection_after_the_winner_commits(self):
        for discarding in (False, True):
            with self.subTest(discarding=discarding):
                with system_context(reason="held race fixture"):
                    row = Message.objects.create(channel_id=self.feed.pk, direction="outbound", status="draft",
                                                 external_id=f"race-{discarding}")
                selection = self.selection(row)
                winner = Message.objects.discard_held_drafts if discarding else Message.objects.send_held_drafts
                loser = Message.objects.send_held_drafts if discarding else Message.objects.discard_held_drafts

                def compete():
                    with actor_context(self.operator):
                        return loser(selection, actor=self.operator)

                before = self.broker.call_count
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with transaction.atomic(), system_context(reason="held action race"):
                        Message.objects.select_for_update().get(pk=row.pk)
                        contender = pool.submit(independent_connection, compete)
                        with actor_context(self.operator):
                            self.assertTrue(winner(selection, actor=self.operator)[0].ok)
                    self.assertFalse(contender.result(timeout=10)[0].ok)
                with system_context(reason="held race settlement check"):
                    row.refresh_from_db()
                    self.assertEqual(row.is_trashed, discarding)
                    self.assertEqual(row.status, "failed" if discarding else "queued")
                self.assertEqual(self.broker.call_count - before, 0 if discarding else 1)
