"""Message source contracts exercised on generated workflow and messaging models."""

from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
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
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, register_steps, run_until

Channel = apps.get_model("messaging.Channel")
Message = apps.get_model("messaging.Message")
Trigger = apps.get_model("workflows.Trigger")
TriggerEvent = apps.get_model("workflows.TriggerEvent")
WorkflowRun = apps.get_model("workflows.WorkflowRun")
StepRun = apps.get_model("workflows.StepRun")
StepWatch = apps.get_model("workflows.StepWatch")


class AcceptMessage(Step[None, None, None]):
    """A neutral admission target that needs no domain mapping."""

    key = "test_accept_message"

    def run(self, ctx):
        """Complete the admitted message."""
        return ctx.done()


class WatchMessage(Step[None, None, None]):
    """Observe the fixed message source without a generic post-save opt-in."""

    key = "test_watch_message"
    subject = "messaging.message"

    def run(self, ctx):
        """Finish after one committed source event resumes the parked body."""
        if ctx.state.get("watching"):
            return ctx.done()
        ctx.watch(ctx.subject_for_update())
        return ctx.wait(state={"watching": True})


class MessageTriggerTests(TransactionTestCase):
    """Scope, actor loss, transactional emission and channel protection compose."""

    def setUp(self):
        """Use real owners, restoring registry overrides and transport after each test."""
        self.enterContext(patch.object(celery_app, "send_task"))
        self.enterContext(register_steps(AcceptMessage, WatchMessage))
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

    def trigger(self, *, channel=None, actor=None, enabled=True):
        """Create disabled configuration, then use the server-owned enable verb."""
        with actor_context(self.admin):
            trigger = Trigger.objects.create(
                workflow=self.workflow, source="message_ingested",
                channel=channel or self.channel,
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
        scoped = self.trigger()
        self.trigger(channel=self.make_channel("other-channel"))
        self.trigger(enabled=False)
        message = self.ingest()
        self.assertEqual(set(system_queryset(TriggerEvent).values_list("trigger_id", flat=True)), {
            scoped.pk,
        })
        self.assertEqual(
            set(system_queryset(TriggerEvent).values_list("record_object_id", flat=True)), {message.pk},
        )

    def test_channel_grant_is_listable_and_disable_revokes_it(self):
        """Message read flows through the channel's direct reader grant."""
        trigger = self.trigger()
        grants = trigger.granted_relationships(actor=self.admin)
        self.assertEqual(len(grants), 1)
        self.assertEqual((grants[0].resource_type, grants[0].resource_id, grants[0].relation), (
            "messaging/channel", str(self.channel.pk), "reader",
        ))
        message = self.ingest()
        self.assertTrue(Message.objects.with_actor(self.workflow.user).filter(pk=message.pk).exists())
        self.assertTrue(self.channel.with_actor(self.workflow.user).has_access("read"))
        self.assertFalse(self.channel.integration_ptr.with_actor(self.workflow.user).has_access("read"))
        Trigger.objects.disable(trigger, actor=self.admin)
        trigger.refresh_from_db()
        self.assertFalse(trigger.enabled)
        self.assertEqual(trigger.granted_relationships(actor=self.admin), ())
        trigger.channel = self.make_channel("replacement-grant-scope")
        trigger.with_actor(self.admin)
        with actor_context(self.admin):
            trigger.save(update_fields=("channel",))
        trigger.refresh_from_db()
        self.assertFalse(trigger.enabled)

    def test_grant_target_label_follows_the_readers_channel_scope(self):
        """Workflow viewers see the tuple, but only channel readers see its label."""
        trigger = self.trigger()
        self.workflow.with_actor(self.admin).grant_record_access("viewer", self.other)
        schema = GraphQLSchemas.from_discovery().build("console")
        query = """query($id: String!) {
          trigger_by_pk(id: $id) { grants { resource_type resource_id relation target_label } }
        }"""
        for actor, label in ((self.admin, self.channel.record_display_label), (self.other, None)):
            with actor_context(actor):
                result = schema.execute_sync(
                    query, variable_values={"id": trigger.sqid},
                    context_value=SimpleNamespace(request=SimpleNamespace(user=actor)),
                )
            self.assertIsNone(result.errors, result.errors)
            self.assertEqual(result.data["trigger_by_pk"]["grants"], [{
                "resource_type": "messaging/channel", "resource_id": str(self.channel.pk),
                "relation": "reader", "target_label": label,
            }])

    def test_non_admin_enable_requires_channel_read(self):
        """Workflow write alone cannot scope a source to an unreadable channel."""
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.other)
        trigger = self.trigger(enabled=False)
        with self.assertRaisesMessage(PermissionDenied, "channel"):
            Trigger.objects.enable(trigger, actor=self.other)
        trigger = system_queryset(Trigger).get(pk=trigger.pk)
        self.assertFalse(trigger.enabled)
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.owner)
        trigger = Trigger.objects.enable(trigger, actor=self.owner)
        self.assertTrue(trigger.enabled)

    def test_channel_reader_cannot_delegate_source_access(self):
        """Reading a channel does not authorize granting its reader tuple."""
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.other)
        self.channel.with_actor(self.owner).grant_record_access("reader", self.other)
        trigger = self.trigger(enabled=False)
        with self.assertRaisesMessage(PermissionDenied, "cannot grant"):
            Trigger.objects.enable(trigger, actor=self.other)
        self.assertFalse(system_queryset(Trigger).get(pk=trigger.pk).enabled)
        granted = Trigger.objects.enable(trigger, actor=self.admin)
        with self.assertRaisesMessage(PermissionDenied, "cannot grant"):
            Trigger.objects.revoke_grant(
                granted, actor=self.other, resource_type="messaging/channel",
                resource_id=str(self.channel.pk), relation="reader",
            )
        self.assertTrue(system_queryset(Trigger).get(pk=trigger.pk).enabled)

    def test_unscoped_message_trigger_cannot_enable_without_a_grant_target(self):
        """A message trigger must name the channel its principal may read."""
        with actor_context(self.admin):
            trigger = Trigger.objects.create(workflow=self.workflow, source="message_ingested", channel=None)
        with self.assertRaisesMessage(ValidationError, "channel grant scope"):
            Trigger.objects.enable(trigger, actor=self.admin)
        self.assertFalse(system_queryset(Trigger).get(pk=trigger.pk).enabled)

    def test_admission_rechecks_channel_read_under_workflow_principal(self):
        """The standing channel grant survives a change in the enabler's reach."""
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.owner)
        self.trigger(actor=self.owner)
        message = self.ingest()
        with system_context(reason="message source transfer channel"):
            self.channel.owner = self.other
            self.channel.save(update_fields=("owner",))
        self.assertTrue(Message.objects.with_actor(self.owner).filter(pk=message.pk).exists())
        for event in system_queryset(TriggerEvent):
            self.assertTrue(Trigger.objects.admit(event))
            event.refresh_from_db()
            self.assertIsNotNone(event.admitted_at)
            self.assertFalse(event.rejection)
        self.assertEqual(system_queryset(WorkflowRun).get().run_as_id, self.workflow.user_id)

    def test_readable_channel_admits_once_for_non_admin(self):
        """The workflow principal owns the run and retained admission survives replays."""
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.owner)
        trigger = self.trigger(actor=self.owner)
        message = self.ingest()
        event = system_queryset(TriggerEvent).get(trigger=trigger)
        self.assertTrue(Trigger.objects.admit(event))
        self.assertFalse(Trigger.objects.admit(event))
        message_ingested.send(sender=Message, instance=message)
        self.assertFalse(Trigger.objects.admit(event))
        run = system_queryset(WorkflowRun).get()
        self.assertEqual(run.run_as_id, self.workflow.user_id)
        self.assertEqual(run.trigger_event_id, event.pk)
        self.assertEqual(run.record_ref.public_id, message.sqid)
        self.assertEqual(run.origin, "trigger")

    def test_scope_edit_requires_a_new_enabler(self):
        """A co-editor cannot borrow the enabling user's authority for another scope."""
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.owner)
        self.workflow.with_actor(self.admin).grant_record_access("editor", self.other)
        trigger = self.trigger(actor=self.owner)
        original_channel = trigger.channel_id
        replacement = self.make_channel("replacement-scope")
        trigger.channel = replacement
        trigger.with_actor(self.other)
        with actor_context(self.other), self.assertRaisesMessage(ValidationError, "Disable the trigger before editing"):
            trigger.save(update_fields=("channel",))
        trigger.refresh_from_db()
        self.assertTrue(trigger.enabled)
        self.assertEqual(trigger.channel_id, original_channel)
        trigger = Trigger.objects.disable(trigger, actor=self.other)
        trigger.with_actor(self.other)
        trigger.channel = replacement
        with actor_context(self.other):
            trigger.save(update_fields=("channel",))
        trigger = Trigger.objects.enable(trigger, actor=self.admin)
        self.assertTrue(trigger.enabled)
        self.assertEqual(trigger.channel_id, replacement.pk)

    def test_admission_rechecks_current_message_channel(self):
        """Moving a pending message cannot bypass its trigger's authored scope."""
        trigger = self.trigger()
        message = self.ingest()
        with system_context(reason="message source move"):
            Message.objects.filter(pk=message.pk).update(channel=self.make_channel("moved-channel"))
        event = system_queryset(TriggerEvent).get()
        self.assertFalse(Trigger.objects.admit(event))
        event.refresh_from_db()
        self.assertIn("channel", event.rejection.lower())
        trigger.refresh_from_db()
        self.assertTrue(trigger.enabled)
        later = self.ingest("message-after-rejection")
        later_event = system_queryset(TriggerEvent).get(record_object_id=later.pk)
        self.assertTrue(Trigger.objects.admit(later_event))
        self.assertEqual(system_queryset(WorkflowRun).count(), 1)

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

    def test_native_message_event_feeds_ledger_and_watches_in_one_transaction(self):
        """The fixed source wakes once after commit; a rolled-back event is inert."""
        self.trigger()
        message = self.ingest()
        workflow = load_workflow(
            {"nodes": {"observe": {"step": WatchMessage.key}}, "results": [{"from": "observe"}]},
            key="message-watch", actor=self.admin, subject_model="messaging.Message",
        )
        workflow.with_actor(self.admin).grant_record_access("starter", self.owner)
        run = WorkflowRun.objects.start(workflow, actor=self.owner, subject=message)
        run_until(run)
        step = system_queryset(StepRun).get(run=run)
        self.assertEqual((step.status, step.waiting_kind), ("waiting", "record"))
        self.assertEqual(system_queryset(StepWatch).get(step_run=step).record_ref.public_id, message.sqid)
        changed = system_queryset(TriggerEvent).get().changed_at

        with self.assertRaisesMessage(ValueError, "rollback"), transaction.atomic():
            message_ingested.send(sender=Message, instance=message)
            raise ValueError("rollback")
        self.assertEqual(runner.wake_records(), 0)
        self.assertEqual(system_queryset(TriggerEvent).get().changed_at, changed)

        with transaction.atomic():
            message_ingested.send(sender=Message, instance=message)
        self.assertEqual(system_queryset(TriggerEvent).count(), 1)
        self.assertGreater(system_queryset(TriggerEvent).get().changed_at, changed)
        self.assertEqual(runner.wake_records(), 1)
        self.assertEqual(runner.wake_records(), 0)
        run_until(run)
        self.assertEqual(run.status, "succeeded")
        self.assertFalse(system_queryset(StepWatch).filter(step_run=step).exists())

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
