"""PostgreSQL delivery interleavings on the actual composed model graph."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

from django.db import close_old_connections, connections, transaction
from django.utils import timezone
from rebac import actor_context, system_context

from angee.integrate.locks import bridge_advisory_lock
from angee.messaging.backends import DeliveryOutcome, DeliveryUncertain
from angee.messaging.managers import DeliveryReconciliationBusy
from tests.native_messaging_delivery import DeliveryFixtures, Message


def independent_connection(call):
    """Keep each competing verb on its own native database session."""

    close_old_connections()
    try:
        return call()
    finally:
        connections.close_all()


class DeliveryConcurrency(DeliveryFixtures):
    def test_operator_cannot_supersede_a_live_reconcile_on_another_connection(self):
        row = self.outbound()
        self.backend.reconciles_delivery = lambda: True
        with system_context(reason="uncertain concurrency fixture"):
            row.deliver()
            claimed = Message.objects.claim_delivery(row.pk, token=row.external_id)
            Message.objects.record_delivery(row.pk, token=row.external_id, claim=claimed.delivery_claim,
                                            error=DeliveryUncertain("Acknowledgement lost."))
            row.refresh_from_db()
        ready, finish = Event(), Event()

        def reconcile():
            with system_context(reason="independent reconcile worker"):
                active = Message.objects.claim_delivery(row.pk, token=row.external_id, reconcile=True)
                self.assertIsNotNone(active)
                ready.set()
                self.assertTrue(finish.wait(10))
                return Message.objects.record_delivery(
                    row.pk, token=row.external_id, claim=active.delivery_claim,
                    outcome=DeliveryOutcome(accepted=True, provider_id="recovered"),
                )

        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(independent_connection, reconcile)
            try:
                self.assertTrue(ready.wait(10))
                for verb in (row.acknowledge_delivery, row.retry_delivery):
                    with actor_context(self.operator), self.assertRaises(DeliveryReconciliationBusy):
                        verb(actor=self.operator, expected_revision=row.revision)
            finally:
                finish.set()
            self.assertEqual(worker.result(timeout=10), "sent")
        with system_context(reason="provider identity survives concurrent operator"):
            row.refresh_from_db()
            self.assertEqual(row.external_id, "recovered")

    def test_row_lock_admits_only_one_effect_claim_without_an_advisory_task_lock(self):
        row = self.outbound()
        with system_context(reason="claim fence fixture"):
            row.deliver()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic(), system_context(reason="effect claim race"):
                Message.objects.select_for_update().get(pk=row.pk)
                contender = pool.submit(independent_connection, lambda: Message.objects.claim_delivery(
                    row.pk, token=row.external_id,
                ))
                winner = Message.objects.claim_delivery(row.pk, token=row.external_id)
                self.assertIsNotNone(winner.delivery_claim)
            self.assertIsNone(contender.result(timeout=10))
        with system_context(reason="effect claim race result"):
            row.refresh_from_db()
        self.assertEqual(row.delivery_claim, winner.delivery_claim)
        self.backend.deliver.assert_not_called()

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
