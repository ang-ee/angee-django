"""Message-event contracts executed only by the isolated composed Django host."""

from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import OperationalError, models
from django.test import TransactionTestCase
from django.utils import timezone
from rebac import system_context

from angee.graphql.schema import GraphQLSchemas
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.workflows import engine
from angee.workflows.models import TriggerKind
from angee.workflows_messaging.models import MESSAGE_INGESTED


class MessageAdmissionTests(TransactionTestCase):
    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        self.Trigger = apps.get_model("workflows", "Trigger")
        self.Message = apps.get_model("messaging", "Message")
        self.Run = apps.get_model("workflows", "WorkflowRun")
        with system_context(reason="test message admission setup"):
            self.actor = get_user_model().objects.create_user(username="message-trigger-owner")
            apps.get_model("integrate", "Vendor").objects.get_or_create(
                slug="manual", defaults={"display_name": "Manual messages"},
            )
            self.channel = apps.get_model("messaging", "Channel").objects.create_disconnected(
                self.actor, name="Messages", backend_class="manual",
            )
            head = apps.get_model("workflows", "Workflow").objects.create(
                name="Process message",
                subject_declaration="messaging.message",
                created_by=self.actor,
            )
            apps.get_model("workflows", "Step").objects.create(
                workflow=head,
                key="start",
                name="Start",
                is_entry=True,
                step_class="wait",
                config={"until": "2099-01-01T00:00:00Z"},
            )
            head.publish()
            self.trigger = self.Trigger.objects.create(
                workflow=head,
                kind=TriggerKind.EVENT,
                message_channel=self.channel,
                execution_actor=self.actor,
                created_by=self.actor,
                config={"model": "messaging.message", "source": MESSAGE_INGESTED},
            )
            self.trigger.enable()
        enqueue = patch.object(engine, "enqueue_advance")
        enqueue.start()
        self.addCleanup(enqueue.stop)

    def ingest(self, external_id="message-1"):
        parsed = ParsedMessage(
            external_id=external_id,
            platform="email",
            subject="Document",
            sender=ParsedHandle(platform="email", value="sender@example.com"),
            body=ParsedPart(type="text/plain", role="body", text="Retained message"),
        )
        with system_context(reason="test message delivery"):
            return self.Message.objects.ingest([parsed], channel=self.channel)

    def test_donor_publisher_is_independent_of_graphql_and_replays_reuse_one_run(self):
        with patch.object(GraphQLSchemas, "change_publisher_models", side_effect=AssertionError("wrong publisher")):
            self.trigger.validated_config(require_publisher=True)
            messages = self.ingest()
            self.ingest()
        self.assertEqual(self.Message._base_manager.count(), 1)
        self.assertEqual(self.Run._base_manager.count(), 1)
        self.assertEqual(self.Run._base_manager.get().subject_object_id, messages[0].pk)

    def test_invalid_trigger_is_disabled_without_rolling_back_the_message(self):
        # Historical rows can predate validation or come from a stale operator write.
        models.QuerySet.update(
            self.Trigger._base_manager.filter(pk=self.trigger.pk),
            config={"model": "messaging.message", "source": MESSAGE_INGESTED, "condition": {"missing": 1}},
        )
        self.ingest()
        self.assertEqual(self.Message._base_manager.count(), 1)
        self.assertEqual(self.Run._base_manager.count(), 0)
        self.trigger.refresh_from_db()
        self.assertFalse(self.trigger.enabled)

    def test_admission_failure_rolls_back_ingest_and_can_be_retried(self):
        with patch.object(
            type(self.Trigger.objects), "start_event", side_effect=OperationalError("database unavailable")
        ):
            with self.assertRaises(OperationalError):
                self.ingest()
        self.assertEqual(self.Message._base_manager.count(), 0)
        self.trigger.refresh_from_db()
        self.assertTrue(self.trigger.enabled)
        self.ingest()
        self.assertEqual(self.Message._base_manager.count(), 1)
        self.assertEqual(self.Run._base_manager.count(), 1)

    def test_donor_rejects_other_publishers_and_ignores_other_subjects(self):
        declaration = self.trigger.validated_config()
        with self.assertRaises(ValidationError):
            self.trigger.event_publisher_model(declaration.model_copy(update={"model": "workflows.workflow"}))
        self.assertIsNone(
            self.Trigger.objects.start_event(
                self.trigger.pk,
                subject=self.trigger.workflow,
                source=MESSAGE_INGESTED,
                occurrence_id="wrong-model",
                timestamp=timezone.now(),
                actor=self.actor,
            )
        )
