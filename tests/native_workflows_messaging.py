"""Message source contracts exercised on generated workflow and messaging models."""

from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.base.scoping import system_queryset
from angee.graphql.deletion import DeletePreview
from angee.graphql.schema import GraphQLSchemas
from angee.jobs.enqueue import celery_app
from angee.messaging.backends import ParsedMessage, ParsedPart
from angee.messaging.events import message_ingested
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, register_steps

Channel = apps.get_model("messaging.Channel")
Message = apps.get_model("messaging.Message")
Trigger = apps.get_model("workflows.Trigger")
TriggerEvent = apps.get_model("workflows.TriggerEvent")
WorkflowRun = apps.get_model("workflows.WorkflowRun")


class AcceptMessage(Step[None, None, None]):
    """A neutral admission target that needs no domain mapping."""

    key = "test_accept_message"

    def run(self, ctx):
        """Complete the admitted message."""
        return ctx.done()


class MessageTriggerTests(TransactionTestCase):
    """Scope, actor loss, transactional emission and channel protection compose."""

    def setUp(self):
        """Use real owners, restoring registry overrides and transport after each test."""
        self.enterContext(patch.object(celery_app, "send_task"))
        self.enterContext(register_steps(AcceptMessage))
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="message source fixtures"):
            self.admin = get_user_model().objects.create_user(username="message-trigger-admin")
            grant_role(actor=self.admin, role="angee/role:admin")
            self.owner = get_user_model().objects.create_user(username="message-trigger-owner")
            self.other = get_user_model().objects.create_user(username="message-trigger-other")
        self.workflow = load_workflow(
            {"nodes": {"accept": {"step": AcceptMessage.key}}, "results": [{"from": "accept"}]},
            key="message-admission", name="Message review", actor=self.admin, subject_model="messaging.Message",
        )
        self.channel = self.make_channel("message-source")

    def make_channel(self, key):
        """Create the native channel connection without external transport."""
        with system_context(reason="message source channel"):
            vendor = apps.get_model("integrate.Vendor").objects.create(slug=key, display_name=key)
            return Channel.objects.create(
                vendor=vendor, owner=self.owner, backend_class="manual", lifecycle="connected",
            )

    def trigger(self, *, channel=None, unscoped=False, actor=None, enabled=True):
        """Create disabled configuration, then use the server-owned enable verb."""
        with actor_context(self.admin):
            trigger = Trigger.objects.create(
                workflow=self.workflow, source="message_ingested",
                channel=None if unscoped else channel or self.channel,
            )
        return Trigger.objects.enable(trigger, actor=actor or self.admin) if enabled else trigger

    def ingest(self, key="message-source-record", **kwargs):
        """Use the actual ingest writer and its existing in-transaction event."""
        parsed = ParsedMessage(
            external_id=key, platform="email", subject="Review requested",
            body=ParsedPart(type="text/plain", role="body", text="Ready for review."),
        )
        with system_context(reason="message source ingest"):
            return Message.objects.ingest([parsed], channel=self.channel, **kwargs)[0]

    def test_scope_records_only_matching_enabled_triggers(self):
        """Different channel and disabled configurations never acquire ledger rows."""
        scoped, unscoped = self.trigger(), self.trigger(unscoped=True)
        self.trigger(channel=self.make_channel("other-channel"))
        self.trigger(enabled=False)
        message = self.ingest()
        self.assertEqual(set(system_queryset(TriggerEvent).values_list("trigger_id", flat=True)), {
            scoped.pk, unscoped.pk,
        })
        self.assertEqual(
            set(system_queryset(TriggerEvent).values_list("record_object_id", flat=True)), {message.pk},
        )

    def test_non_admin_enable_requires_channel_read(self):
        """Workflow write alone cannot scope a source to an unreadable channel."""
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.other)
        trigger = self.trigger(enabled=False)
        with self.assertRaisesMessage(PermissionDenied, "channel"):
            Trigger.objects.enable(trigger, actor=self.other)
        trigger = system_queryset(Trigger).get(pk=trigger.pk)
        self.assertFalse(trigger.enabled)
        self.assertIsNone(trigger.run_as_id)
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.owner)
        trigger = Trigger.objects.enable(trigger, actor=self.owner)
        self.assertEqual(trigger.run_as_id, self.owner.pk)

    def test_admission_rechecks_channel_read_even_with_message_read(self):
        """A former channel owner retains authored message read, but loses admission."""
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.owner)
        for unscoped in (False, True):
            self.trigger(unscoped=unscoped, actor=self.owner)
        message = self.ingest()
        with system_context(reason="message source transfer channel"):
            self.channel.owner = self.other
            self.channel.save(update_fields=("owner",))
        self.assertTrue(Message.objects.with_actor(self.owner).filter(pk=message.pk).exists())
        for event in system_queryset(TriggerEvent):
            self.assertFalse(Trigger.objects.admit(event))
            event.refresh_from_db()
            self.assertIsNone(event.admitted_at)
            self.assertIn("channel", event.rejection.lower())
        self.assertFalse(system_queryset(WorkflowRun).exists())

    def test_readable_channel_admits_once_for_non_admin(self):
        """The enabling user owns the run and the retained admission survives replays."""
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.owner)
        trigger = self.trigger(actor=self.owner)
        message = self.ingest()
        event = system_queryset(TriggerEvent).get(trigger=trigger)
        self.assertTrue(Trigger.objects.admit(event))
        self.assertFalse(Trigger.objects.admit(event))
        message_ingested.send(sender=Message, instance=message)
        self.assertFalse(Trigger.objects.admit(event))
        run = system_queryset(WorkflowRun).get()
        self.assertEqual(run.run_as_id, self.owner.pk)
        self.assertEqual(run.trigger_event_id, event.pk)
        self.assertEqual(run.record_ref.public_id, message.sqid)
        self.assertEqual(run.origin, "trigger")

    def test_admission_rechecks_current_message_channel(self):
        """Moving a pending message cannot bypass its trigger's authored scope."""
        self.trigger()
        message = self.ingest()
        with system_context(reason="message source move"):
            Message.objects.filter(pk=message.pk).update(channel=self.make_channel("moved-channel"))
        event = system_queryset(TriggerEvent).get()
        self.assertFalse(Trigger.objects.admit(event))
        event.refresh_from_db()
        self.assertIn("channel", event.rejection.lower())

    def test_protection_and_preview_name_the_retaining_trigger(self):
        """Disabled scopes retain their channel; names respect trigger visibility."""
        trigger = self.trigger(enabled=False)
        message = self.ingest()
        with actor_context(self.admin):
            preview = DeletePreview.from_counts(
                self.channel, Channel.objects.inventory(self.channel), blockers=self.channel.purge_blockers(),
            )
            self.assertTrue(preview.has_blockers)
            self.assertEqual(preview.total_deleted_count, 0)
            self.assertEqual(preview.root.children[0].children[0].object_label, str(trigger))
            self.assertEqual(preview.root.children[0].children[0].object_id, trigger.sqid)
            with self.assertRaises(ProtectedError):
                Channel.objects.purge(self.channel)
        self.assertTrue(system_queryset(Message).filter(pk=message.pk).exists())
        self.assertTrue(system_queryset(Channel).filter(pk=self.channel.pk).exists())
        with actor_context(self.owner):
            hidden = DeletePreview.from_counts(self.channel, {}, blockers=self.channel.purge_blockers())
        self.assertTrue(hidden.has_blockers)
        self.assertTrue(all(leaf.object_id is None for group in hidden.root.children for leaf in group.children))
        self.assertNotIn(str(trigger), [leaf.object_label for group in hidden.root.children for leaf in group.children])
        schema = GraphQLSchemas.from_discovery().build("console")
        for confirm in (False, True):
            with actor_context(self.admin):
                result = schema.execute_sync(
                    """mutation($id: ID!, $confirm: Boolean!) {
                      delete_channel(id: $id, confirm: $confirm) {
                        has_blockers root { children { children { object_id object_label } } }
                      }
                    }""",
                    variable_values={"id": self.channel.sqid, "confirm": confirm},
                    context_value=SimpleNamespace(request=SimpleNamespace(user=self.admin)),
                )
            self.assertIsNone(result.errors, result.errors)
            result_preview = result.data["delete_channel"]
            self.assertTrue(result_preview["has_blockers"])
            self.assertEqual(result_preview["root"]["children"][0]["children"], [{
                "object_id": trigger.sqid, "object_label": str(trigger),
            }])
        self.assertTrue(system_queryset(Message).filter(pk=message.pk).exists())

    def test_explicit_bulk_write_notification_rearms_the_same_ledger(self):
        """Queryset writes notify through the native source signal inside the write."""
        self.trigger()
        message = self.ingest()
        changed = system_queryset(TriggerEvent).get().changed_at
        with system_context(reason="message source explicit write"), transaction.atomic():
            Message.objects.filter(pk=message.pk).update(status="failed")
            message.refresh_from_db()
            message_ingested.send(sender=Message, instance=message)
        self.assertGreater(system_queryset(TriggerEvent).get().changed_at, changed)

    def test_live_signal_rolls_back_and_historical_replay_stays_silent(self):
        """Capture follows message commits and the messaging owner's replay rules."""
        self.trigger()
        with self.assertRaisesMessage(ValueError, "rollback"), transaction.atomic():
            self.ingest("rolled-back-message")
            self.assertEqual(system_queryset(TriggerEvent).count(), 1)
            raise ValueError("rollback")
        self.assertFalse(system_queryset(TriggerEvent).exists())
        self.ingest("historical-message", historical=True)
        self.assertFalse(system_queryset(TriggerEvent).exists())
        self.ingest()
        changed = system_queryset(TriggerEvent).get().changed_at
        self.ingest()
        self.assertEqual(system_queryset(TriggerEvent).get().changed_at, changed)

    def test_channel_scope_resource_composes_relation_write_filter_and_read(self):
        """The satellite's field declaration extends the canonical resource input."""
        schema = GraphQLSchemas.from_discovery().build("console")
        query = """mutation($workflow: ID!, $channel: ID!) {
          insert_trigger_one(object: {workflow: $workflow, channel: $channel, source: "message_ingested"}) {
            id channel { id }
          }
        }"""
        with actor_context(self.admin):
            result = schema.execute_sync(
                query, variable_values={"workflow": self.workflow.sqid, "channel": self.channel.sqid},
                context_value=SimpleNamespace(request=SimpleNamespace(user=self.admin)),
            )
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data["insert_trigger_one"]["channel"]["id"], self.channel.sqid)
        trigger_id = result.data["insert_trigger_one"]["id"]
        with actor_context(self.admin):
            filtered = schema.execute_sync(
                """query($channel: String!) {
                  trigger(where: {channel: {_eq: $channel}}) { id channel { id } }
                }""",
                variable_values={"channel": self.channel.sqid},
                context_value=SimpleNamespace(request=SimpleNamespace(user=self.admin)),
            )
        self.assertIsNone(filtered.errors, filtered.errors)
        self.assertEqual(filtered.data["trigger"], [{"id": trigger_id, "channel": {"id": self.channel.sqid}}])
        self.workflow.with_actor(self.admin).grant_record_access("viewer", self.other)
        with actor_context(self.other):
            hidden = schema.execute_sync(
                "query($id: String!) { trigger_by_pk(id: $id) { id channel { id } } }",
                variable_values={"id": trigger_id},
                context_value=SimpleNamespace(request=SimpleNamespace(user=self.other)),
            )
        self.assertIsNone(hidden.errors, hidden.errors)
        self.assertEqual(hidden.data["trigger_by_pk"], {"id": trigger_id, "channel": None})
