"""Intake capture contracts executed only by the isolated composed Django host."""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import system_context

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart


class ChannelIntakeCaptureTests(TransactionTestCase):
    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        self.Message = apps.get_model("messaging", "Message")
        self.Need = apps.get_model("intake", "Need")
        with system_context(reason="test intake capture setup"):
            self.actor = get_user_model().objects.create_user(username="intake-owner")
            apps.get_model("integrate", "Vendor").objects.get_or_create(
                slug="manual", defaults={"display_name": "Manual messages"},
            )
            self.queue = apps.get_model("work", "Queue").objects.personal_for(self.actor, provision=True)
            self.channel = apps.get_model("messaging", "Channel").objects.create_disconnected(
                self.actor, name="Support", backend_class="manual",
            )

    def ingest(self, external_id="support-1"):
        parsed = ParsedMessage(
            external_id=external_id,
            platform="email",
            subject="Printer is on fire",
            sender=ParsedHandle(platform="email", value="customer@example.com"),
            body=ParsedPart(type="text/plain", role="body", text="Please help"),
        )
        with system_context(reason="test intake ingest"):
            return self.Message.objects.ingest([parsed], channel=self.channel)

    def test_message_on_an_intake_channel_captures_one_need(self):
        with system_context(reason="test intake queue"):
            self.channel.intake_queue = self.queue
            self.channel.save(update_fields=["intake_queue"])
        (message,) = self.ingest()
        self.ingest()
        need = self.Need._base_manager.get()
        self.assertEqual(need.source_message_id, message.pk)
        self.assertEqual(self.Need._base_manager.count(), 1)

    def test_message_on_a_channel_without_queue_captures_nothing(self):
        self.ingest()
        self.assertEqual(self.Message._base_manager.count(), 1)
        self.assertEqual(self.Need._base_manager.count(), 0)

    def test_transport_channel_is_the_concrete_channel_child(self):
        (message,) = self.ingest()
        channel = message.transport_channel(reason="test transport channel")
        self.assertIs(type(channel), apps.get_model("messaging", "Channel"))
        self.assertEqual(channel.pk, self.channel.pk)
