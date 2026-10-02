"""Intake contracts executed only by the isolated composed Django host."""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, models
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from graphql import parse, validate
from rebac import PermissionDenied, actor_context, system_context
from rebac.actors import is_sudo, to_subject_ref
from rebac.roles import grant as grant_role

from angee.base.errors import RecordAccessSubjectRefused
from angee.base.mixins import StaleRevisionError
from angee.graphql.schema import GraphQLSchemas
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart


class ChannelIntakeCaptureTests(TransactionTestCase):
    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        self.Message = apps.get_model("messaging", "Message")
        self.Need = apps.get_model("intake", "Need")
        with system_context(reason="test intake capture setup"):
            self.actor = get_user_model().objects.create_user(username="intake-owner")
            apps.get_model("integrate", "Vendor").objects.get_or_create(
                slug="manual",
                defaults={"display_name": "Manual messages"},
            )
            self.queue = apps.get_model("work", "Queue").objects.personal_for(self.actor, provision=True)
            self.channel = apps.get_model("messaging", "Channel").objects.create_disconnected(
                self.actor,
                name="Support",
                backend_class="manual",
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

    def webform(self, *, domains=()):
        with system_context(reason="test form setup"):
            apps.get_model("integrate", "Vendor").objects.get_or_create(
                slug="webform",
                defaults={"display_name": "Webform"},
            )
            channel = apps.get_model("messaging", "Channel").objects.create_disconnected(
                self.actor,
                name="Request form",
                backend_class="webform",
                slug="request-form",
                form_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "subject": {"type": "string"},
                        "email": {"type": "string", "widget": "email"},
                        "size": {"type": "integer"},
                    },
                },
            )
            channel.intake_queue = self.queue
            channel.intake_field_map = {"title": "subject", "claimed_name": "name", "estimate": "size"}
            channel.intake_requester_domains = list(domains)
            channel.full_clean()
            channel.save()
        return channel

    def submit(self, channel, key="form-1"):
        parsed = channel.webform_message(
            submission_id=key,
            answers={
                "name": "Alex Requester",
                "subject": "New request",
                "email": "alex@example.com",
                "size": 3,
            },
        )
        with system_context(reason="test form ingest"):
            return self.Message.objects.ingest([parsed], channel=channel)

    def test_webform_captures_claims_and_mapped_fields_on_an_ownerless_task(self):
        channel = self.webform()
        (message,) = self.submit(channel)
        self.submit(channel)
        need = self.Need._base_manager.get()
        self.assertEqual(need.source_message_id, message.pk)
        self.assertEqual((need.claimed_name, need.claimed_email), ("Alex Requester", "alex@example.com"))
        self.assertEqual((need.task.title, need.task.estimate), ("New request", 3))
        self.assertIsNone(need.task.owner_id)
        self.assertIsNone(need.party_id)
        self.assertIsNone(need.project_id)
        self.assertEqual(need.access_verdict, "pending")
        self.assertIsNotNone(need.access_decision_id)
        self.assertIsNone(need.access_decision.requester_id)
        self.assertIsNone(need.access_decision.group.issuer_id)

    def test_domain_capture_reuses_one_account_without_credentials(self):
        channel = self.webform(domains=("example.com",))
        self.submit(channel)
        self.submit(channel, "form-2")
        users = get_user_model()._base_manager.filter(email="alex@example.com")
        self.assertEqual(users.count(), 1)
        self.assertFalse(users.get().has_usable_password())
        needs = list(self.Need._base_manager.order_by("pk"))
        self.assertEqual(len(needs), 2)
        self.assertIsNotNone(needs[0].party_id)
        self.assertEqual(needs[0].party_id, needs[1].party_id)

    def test_domain_capture_skips_requester_follow_without_record_read(self):
        channel = self.webform(domains=("example.com",))
        self.submit(channel)
        need = self.Need._base_manager.get()
        requester = get_user_model()._base_manager.get(email="alex@example.com")
        self.assertFalse(need.task.thread_reader_allowed(requester))
        self.assertFalse(need.task.message_is_follower(user=requester))

    def test_requester_role_admission_and_removal_track_the_read(self):
        channel = self.webform()
        self.submit(channel)
        need = self.Need._base_manager.get()
        with system_context(reason="test.intake.requester_role"):
            requester = get_user_model().objects.create_user(username="requester-role-person", kind="person")
            need.with_actor(self.actor).admit_requester(requester)
            self.assertTrue(need.task.thread_reader_allowed(requester))
            self.assertTrue(need.task.message_is_follower(user=requester))
        need.remove_requester()
        with system_context(reason="test.intake.requester_role.verify_removal"):
            self.assertFalse(need.task.thread_reader_allowed(requester))
            self.assertFalse(need.task.message_is_follower(user=requester))

    def test_refused_email_holder_retains_capture_and_rolls_back_identity(self):
        channel = self.webform(domains=("example.com",))
        task_model = apps.get_model("projects", "Task")
        with patch.object(task_model, "validate_record_access_subject", side_effect=RecordAccessSubjectRefused()):
            self.submit(channel)
        need = self.Need._base_manager.get()
        self.assertIsNone(need.party_id)
        self.assertTrue(task_model._base_manager.filter(pk=need.task_id).exists())
        self.assertFalse(get_user_model()._base_manager.filter(email="alex@example.com").exists())

    def test_refused_confirmed_sender_retains_capture_and_source_attachment(self):
        with system_context(reason="test confirmed sender"):
            self.channel.intake_queue = self.queue
            self.channel.save(update_fields=("intake_queue",))
            party = apps.get_model("parties", "Party").objects.for_user(self.actor)
            handle = apps.get_model("parties", "Handle").objects.upsert(
                platform="email",
                value="customer@example.com",
            )
            apps.get_model("parties", "PartyHandle").objects.link(party, handle, is_confirmed=True)
        task_model = apps.get_model("projects", "Task")
        with patch.object(
            task_model, "validate_record_access_subject", side_effect=RecordAccessSubjectRefused()
        ) as hook:
            (message,) = self.ingest()
        hook.assert_called()
        need = self.Need._base_manager.get()
        self.assertEqual(need.source_message_id, message.pk)
        self.assertIsNone(need.party_id)
        self.assertTrue(task_model._base_manager.filter(pk=need.task_id).exists())
        self.assertTrue(
            apps.get_model("messaging", "ThreadAttachment")
            ._base_manager.filter(
                thread_id=message.thread_id,
            )
            .exists()
        )

    def test_programming_error_rolls_back_ingest(self):
        channel = self.webform()
        with patch.object(self.Message, "webform_submission", side_effect=TypeError("Invalid contract")):
            with self.assertRaisesMessage(TypeError, "Invalid contract"):
                self.submit(channel)
        self.assertEqual(self.Message._base_manager.count(), 0)
        self.assertEqual(self.Need._base_manager.count(), 0)


class IntakeAccessCase(TransactionTestCase):
    """Real account, task, party and permission owners shared by access contracts."""

    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        self.Need = apps.get_model("intake", "Need")
        self.Task = apps.get_model("projects", "Task")
        self.Party = apps.get_model("parties", "Party")
        self.User = get_user_model()
        with system_context(reason="test request access setup"):
            self.owner = self.User.objects.create_user(username="request-owner", email="owner@example.com")
            self.writer = self.User.objects.create_user(username="request-writer", email="writer@example.com")
            self.reader = self.User.objects.create_user(username="request-reader", email="reader@example.com")
            self.admin = self.User.objects.create_user(username="request-admin", email="admin@example.com")
            grant_role(actor=self.admin, role="angee/role:admin")
            self.queue = apps.get_model("work", "Queue").objects.personal_for(self.owner, provision=True)

    def need(self, *, email="new@example.com", party=None):
        with system_context(reason="test request setup"):
            task = self.Need.objects._create_triage_task(queue=self.queue, title="Request", note="")
            task.owner = self.owner
            task.assignee = self.writer
            task.save()
            task.grant_record_access("reader", self.reader)
            return self.Need.objects.create(task=task, party=party, claimed_email=email, body="Request")

    def as_user(self, need, user=None):
        return self.Need.objects.as_user(user or self.admin).get(pk=need.pk)

    def party(self, user):
        with system_context(reason="test account identity"):
            return self.Party.objects.for_user(user)

    def graphql(self, query, variables, *, user=None, bucket="public", error_code=None):
        user = user or self.admin
        request = RequestFactory().post("/graphql/")
        request.user = user
        with actor_context(user):
            result = (
                GraphQLSchemas.from_discovery()
                .build(bucket)
                .execute_sync(
                    query,
                    variable_values=variables,
                    context_value=SimpleNamespace(request=request),
                )
            )
        if error_code is None:
            self.assertIsNone(result.errors, result.errors)
        else:
            self.assertEqual([error.extensions.get("code") for error in result.errors or ()], [error_code])
        return result.data

    def legacy_need(self, *, confirmed=False, touched=False):
        """Reproduce a pre-transition copied sender party without live hooks."""

        with system_context(reason="test legacy capture"):
            apps.get_model("integrate", "Vendor").objects.get_or_create(
                slug="manual",
                defaults={"display_name": "Manual"},
            )
            channel = apps.get_model("messaging", "Channel").objects.create_disconnected(
                self.owner,
                name="Legacy source",
                backend_class="manual",
            )
            (message,) = apps.get_model("messaging", "Message").objects.ingest(
                [
                    ParsedMessage(
                        external_id=f"legacy-{channel.pk}",
                        platform="email",
                        sender=ParsedHandle(platform="email", value=f"legacy-{channel.pk}@example.com"),
                        body=ParsedPart(type="text/plain", role="body", text="Legacy request"),
                    ),
                ],
                channel=channel,
            )
            party = self.party(self.reader)
            models.QuerySet.update(
                type(message.sender)._base_manager.filter(pk=message.sender_id),
                party_id=party.pk,
                party_link_confirmed=confirmed,
            )
            need = self.need(email="", party=party)
            models.QuerySet.update(
                self.Need._base_manager.filter(pk=need.pk),
                source_message_id=message.pk,
                updated_by_id=self.writer.pk if touched else message.updated_by_id,
            )
            need.refresh_from_db()
            return need


class NeedAccessDecisionTests(IntakeAccessCase):
    def test_inbox_answers_use_the_same_account_owner_and_write_no_grants(self):
        need = self.need(email="", party=self.party(self.reader))
        decision = need.access_decision
        stores = [apps.get_model("rebac", name) for name in ("Relationship", "RelationshipRegistry")]
        before = [list(store._base_manager.order_by("pk").values()) for store in stores]
        mutation = """
          mutation Decide($id: ID!, $revision: Int!) {
            decide(id: $id, revision: $revision, action: "intake.approve", values: {}) {
              ok validation_errors
            }
          }
        """
        variables = {"id": str(decision.sqid), "revision": decision.revision}
        with self.assertRaises(PermissionDenied):
            decision.decide(actor=self.writer, revision=decision.revision, action="intake.approve", values={})
        need.refresh_from_db()
        self.assertEqual(need.access_verdict, "pending")
        accepted = self.graphql(mutation, variables, bucket="console")
        self.assertTrue(accepted["decide"]["ok"])
        need.refresh_from_db()
        self.assertEqual(need.access_verdict, "completed")
        self.assertEqual(need.access_resolved_by_id, self.admin.pk)
        self.assertEqual(need._account_for_party(need.party_id).pk, self.reader.pk)
        self.assertEqual([list(store._base_manager.order_by("pk").values()) for store in stores], before)

    def test_stale_request_revision_preserves_the_native_conflict_code(self):
        need = self.as_user(self.need())
        revision = need.revision
        need.decide_access("intake.deny")
        self.graphql("""
          mutation Decide($need: ID!, $revision: Int!) {
            decide_need_access(need: $need, action: INTAKE_APPROVE, expected_revision: $revision) {
              ok code
            }
          }
        """, {"need": str(need.sqid), "revision": revision}, error_code="STALE_REVISION")
        need.refresh_from_db()
        self.assertEqual(need.access_verdict, "rejected")

    def test_action_schema_accepts_only_declared_decisions(self):
        schema = GraphQLSchemas.from_discovery().graphql_schema("public")
        action = schema.mutation_type.fields["decide_need_access"].args["action"].type.of_type
        self.assertEqual(set(action.values), {"INTAKE_APPROVE", "INTAKE_DENY"})

    def test_deny_replay_stale_revision_then_approve_once(self):
        need = self.as_user(self.need(), self.owner)
        before = need.revision
        need.decide_access("intake.deny", reason="Not yet", expected_revision=before)
        denied = need.access_decision
        self.assertEqual(need.access_verdict, "rejected")
        self.assertEqual(need.access_resolution, {"action": "intake.deny", "reason": "Not yet"})
        self.assertEqual(need.access_resolved_by_id, self.owner.pk)
        self.assertEqual(need.revision, before + 1)
        self.assertIsNotNone(need.access_resolved_at)
        need.decide_access("intake.deny", reason="Replay")
        self.assertEqual(need.revision, before + 1)
        self.assertEqual(need.access_resolution["reason"], "Not yet")
        with self.assertRaises(PermissionDenied):
            need.decide_access("intake.approve")
        need = self.as_user(need)
        with self.assertRaises(StaleRevisionError):
            need.decide_access("intake.approve", expected_revision=before)
        account = need.decide_access("intake.approve", reason="Granted", expected_revision=before + 1)
        self.assertEqual(need.revision, before + 2)
        self.assertEqual(need.access_verdict, "completed")
        self.assertEqual(need.access_resolution, {"action": "intake.approve", "reason": "Granted"})
        denied.refresh_from_db()
        self.assertEqual(need.access_decision.group.reasked_from_id, denied.group_id)
        self.assertEqual(denied.resolution, {"action": "intake.deny", "reason": "Not yet"})
        self.assertEqual(denied.closed_reason, "resolved")
        self.assertFalse(account.has_usable_password())
        self.assertEqual(need.decide_access("intake.approve").pk, account.pk)
        self.assertEqual(need.revision, before + 2)
        with self.assertRaisesMessage(ValidationError, "Approved access is final"):
            need.decide_access("intake.deny")
        self.assertEqual(self.User._base_manager.filter(email="new@example.com").count(), 1)

    def test_existing_account_without_claimed_email_can_be_approved(self):
        need = self.as_user(self.need(email="", party=self.party(self.reader)))
        before = need.revision
        self.assertEqual(need.decide_access("intake.approve").pk, self.reader.pk)
        self.assertEqual(need.revision, before + 1)
        self.assertEqual(need.access_verdict, "completed")

    def test_conflicts_are_in_band_and_do_not_overwrite_an_account(self):
        for email, party, message in (
            ("", None, "This request has no claimed email."),
        ):
            with self.subTest(email=email):
                need = self.need(email=email, party=party)
                result = self.graphql(
                    """
                    mutation($need: ID!) {
                      decide_need_access(need: $need, action: INTAKE_APPROVE) { ok validation_errors }
                    }
                """,
                    {"need": need.sqid},
                )["decide_need_access"]
                self.assertEqual(result, {"ok": False, "validation_errors": {"conflict": [message]}})
                need.refresh_from_db()
                self.assertEqual(need.party_id, party.pk if party else None)
                self.assertEqual(need.access_verdict, "pending")
        self.assertFalse(self.User._base_manager.filter(email="unknown@example.com").exists())

    def test_existing_active_party_wins_over_a_different_or_unknown_claimed_email(self):
        for email in (self.writer.email, "unknown@example.com", "not-an-email"):
            with self.subTest(email=email):
                party = self.party(self.reader)
                need = self.as_user(self.need(email=email, party=party))
                self.assertEqual(need.decide_access("intake.approve").pk, self.reader.pk)
                self.assertEqual(need.party_id, party.pk)
                self.assertEqual(need.access_verdict, "completed")
        self.assertFalse(self.User._base_manager.filter(email="unknown@example.com").exists())

    def test_inactive_account_is_refused(self):
        with system_context(reason="test inactive account"):
            self.reader.is_active = False
            self.reader.save(update_fields=("is_active",))
        for party in (None, self.party(self.reader)):
            with self.subTest(party=party):
                need = self.as_user(self.need(email=self.reader.email, party=party))
                with self.assertRaisesMessage(ValidationError, "inactive"):
                    need.decide_access("intake.approve")
                need.refresh_from_db()
                self.assertEqual(need.access_verdict, "pending")

    def test_party_change_or_clear_resets_decision_in_the_same_save(self):
        for replacement in (self.party(self.writer), None):
            for partial in (False, True):
                with self.subTest(replacement=replacement, partial=partial):
                    need = self.as_user(self.need(email="", party=self.party(self.reader)))
                    need.decide_access("intake.approve")
                    previous_decision = need.access_decision
                    before = need.revision
                    need.party = replacement
                    need.save(**({"update_fields": ("party",)} if partial else {}))
                    need.refresh_from_db()
                    self.assertEqual(need.revision, before + 1)
                    self.assertEqual(need.access_verdict, "pending")
                    self.assertIsNone(need.access_resolved_by_id)
                    self.assertIsNone(need.access_resolved_at)
                    self.assertEqual(need.access_resolution, {})
                    previous_decision.refresh_from_db()
                    self.assertEqual(need.access_decision.group.reasked_from_id, previous_decision.group_id)
                    self.assertEqual(previous_decision.verdict, "completed")

    def test_unchanged_party_and_unpersisted_assignment_keep_decision(self):
        need = self.as_user(self.need(email="", party=self.party(self.reader)))
        need.decide_access("intake.approve")
        need.save()
        need.party = self.party(self.writer)
        need.body = "Edited request"
        need.save(update_fields=("body",))
        need.refresh_from_db()
        self.assertEqual(need.access_verdict, "completed")
        self.assertEqual(need.party_id, self.party(self.reader).pk)

    def test_reconcile_never_yields_system_context_and_clears_old_decision(self):
        need = self.legacy_need()
        apps.get_model("decisions", "Decision").objects.decide(
            need.access_decision_id, actor=self.owner, revision=need.access_decision.revision,
            action="intake.approve", values={},
        )
        self.assertFalse(is_sudo())
        reports = self.Need.objects.reconcile_parties()
        self.assertTrue(next(reports)["eligible"])
        self.assertFalse(is_sudo())
        reports.close()
        self.assertIsNotNone(self.Need._base_manager.get(pk=need.pk).party_id)
        reports = self.Need.objects.reconcile_parties(apply=True)
        self.assertTrue(next(reports)["applied"])
        self.assertFalse(is_sudo())
        reports.close()
        need.refresh_from_db()
        self.assertIsNone(need.party_id)
        self.assertEqual(need.access_verdict, "pending")
        self.assertEqual(need.access_resolution, {})



    def test_deciding_writes_neither_relationship_store(self):
        need = self.as_user(self.need(email="", party=self.party(self.reader)))
        stores = [apps.get_model("rebac", name) for name in ("Relationship", "RelationshipRegistry")]
        before = [list(store._base_manager.order_by("pk").values()) for store in stores]
        need.decide_access("intake.deny")
        need.decide_access("intake.approve")
        self.assertEqual([list(store._base_manager.order_by("pk").values()) for store in stores], before)

    def test_access_projections_batch_one_seat_query_for_lists(self):
        query = "query { intake_needs { access_verdict access_resolved_at access_resolution } }"
        for count in (1, 5):
            while self.Need._base_manager.count() < count:
                self.need()
            # Warm schema and permission caches independently of list size.
            self.graphql(query, {})
            with CaptureQueriesContext(connection) as captured:
                values = self.graphql(query, {})["intake_needs"]
            self.assertEqual(len(values), count)
            self.assertTrue(all(value["access_verdict"] == "PENDING" for value in values))
            table = apps.get_model("decisions", "Decision")._meta.db_table
            seat_reads = [item["sql"] for item in captured if f'FROM "{table}"' in item["sql"]]
            self.assertEqual(len(seat_reads), 1, seat_reads)


class NeedAccessTests(IntakeAccessCase):
    def test_task_requester_projects_name_and_writer_only_email_in_both_schemas(self):
        party = self.party(self.reader)
        need = self.need(email="contact@example.com", party=party)
        query = "{ project_tasks { id requester { display_name email } } }"
        for bucket in ("public", "console"):
            writer = self.graphql(query, {}, user=self.writer, bucket=bucket)["project_tasks"]
            reader = self.graphql(query, {}, user=self.reader, bucket=bucket)["project_tasks"]
            self.assertEqual(writer, [{"id": need.task.sqid, "requester": {
                "display_name": party.display_name, "email": "contact@example.com",
            }}])
            self.assertEqual(reader, [{"id": need.task.sqid, "requester": {
                "display_name": party.display_name, "email": None,
            }}])

    def test_decision_inbox_tracks_current_sharers_without_assignment_snapshots(self):
        need = self.need()
        decisions = apps.get_model("decisions", "Decision")
        groups = apps.get_model("decisions", "DecisionGroup")
        decision = need.access_decision
        self.assertFalse(decision.assignees.sudo(reason="tests.intake.no_assignee_snapshot").exists())
        for user, allowed in ((self.owner, True), (self.writer, False), (self.reader, False)):
            self.assertEqual(decisions.objects.as_user(user).filter(pk=decision.pk).exists(), allowed)
            self.assertEqual(groups.objects.as_user(user).filter(pk=decision.group_id).exists(), allowed)
            self.assertEqual(decision.with_actor(to_subject_ref(user)).has_access("act"), allowed)
        with actor_context(self.owner):
            task = need.task.with_actor(to_subject_ref(self.owner))
            task.grant_record_access("editor", self.reader)
        self.assertTrue(decisions.objects.as_user(self.reader).filter(pk=decision.pk).exists())
        self.assertTrue(decision.with_actor(to_subject_ref(self.reader)).has_access("act"))
        with actor_context(self.owner):
            task.revoke_record_access("editor", self.reader)
        self.assertFalse(decisions.objects.as_user(self.reader).filter(pk=decision.pk).exists())
        self.assertFalse(decision.with_actor(to_subject_ref(self.reader)).has_access("act"))

    def test_writer_cannot_assign_party_on_insert_update_full_save_or_capture(self):
        need = self.need()
        party = self.party(self.reader)
        for partial in (True, False):
            candidate = self.as_user(need, self.writer)
            candidate.party = party
            with self.assertRaises(PermissionDenied):
                candidate.save(**({"update_fields": ("party",)} if partial else {}))
        with actor_context(self.writer):
            with self.assertRaises(PermissionDenied):
                self.Need(task=need.task, party=party).save()
            with self.assertRaises(PermissionDenied):
                self.Need.objects.capture(target=need.task, body="Manual request", party=party)
        candidate = self.as_user(need, self.owner)
        candidate.party = party
        candidate.save(update_fields=("party",))
        self.assertEqual(self.Need._base_manager.get(pk=need.pk).party_id, party.pk)
        self.assertTrue(apps.get_model("messaging", "ThreadFollower")._base_manager.filter(party_id=party.pk).exists())

    def test_holder_hook_refuses_elevated_assignment_and_approval_atomically(self):
        need = self.need()
        party = self.party(self.reader)
        with patch.object(self.Task, "validate_record_access_subject", side_effect=RecordAccessSubjectRefused()):
            with system_context(reason="test holder refusal"):
                with self.assertRaises(RecordAccessSubjectRefused):
                    self.Need.objects.create(task=need.task, party=party)
            with self.assertRaises(RecordAccessSubjectRefused):
                self.as_user(need).decide_access("intake.approve")
        self.assertFalse(self.User._base_manager.filter(email="new@example.com").exists())
        need.refresh_from_db()
        self.assertIsNone(need.party_id)
        self.assertEqual(need.access_verdict, "pending")

    def test_assignment_uses_target_share_and_preserves_actor_for_partial_full_and_insert_saves(self):
        party = self.party(self.reader)
        self.assertFalse(party.with_actor(self.owner).has_access("write"))
        for partial in (True, False):
            need = self.as_user(self.need(), self.owner)
            need.party = party
            need.body = "Assigned request"
            need.save(**({"update_fields": ("party",)} if partial else {}))
            self.assertFalse(need.is_sudo())
            stored = self.Need._base_manager.get(pk=need.pk)
            self.assertEqual(stored.party_id, party.pk)
            self.assertEqual(stored.updated_by_id, self.owner.pk)
            self.assertEqual(stored.body, "Request" if partial else "Assigned request")
        inserted = self.Need(task=need.task, party=party, body="New request").with_actor(self.owner)
        inserted.save()
        self.assertFalse(inserted.is_sudo())
        self.assertEqual(inserted.updated_by_id, self.owner.pk)
        self.assertEqual(self.Need._base_manager.get(pk=inserted.pk).party_id, party.pk)

    def test_need_writer_cannot_move_its_party_to_a_target_the_writer_owns(self):
        need = self.need()
        original_task_id = need.task_id
        target = self.need().task
        with system_context(reason="test.intake.assignment_target"):
            target.owner = self.writer
            target.save(update_fields=("owner",))
        candidate = self.as_user(need, self.writer)
        self.assertTrue(target.with_actor(self.writer).has_access("share"))
        self.assertTrue(candidate.has_access("write"))
        self.assertFalse(candidate.has_access("share"))
        candidate.task = target
        candidate.party = self.party(self.reader)
        with self.assertRaises(PermissionDenied):
            candidate.save(update_fields=("task", "party"))
        stored = self.Need._base_manager.get(pk=need.pk)
        self.assertEqual(stored.task_id, original_task_id)
        self.assertIsNone(stored.party_id)

    def test_field_redaction_does_not_erase_decisions_on_writer_save(self):
        need = self.as_user(self.need(), self.owner)
        need.decide_access("intake.deny", reason="Private reason")
        reader = self.as_user(need, self.reader)
        self.assertIsNone(reader.claimed_email)
        writer = self.as_user(need, self.writer)
        self.assertEqual(writer.claimed_email, "new@example.com")
        for candidate in (reader, writer):
            self.assertIsNone(candidate.access_verdict)
            self.assertIsNone(candidate.access_resolved_by_id)
            self.assertIsNone(candidate.access_resolved_at)
            self.assertIsNone(candidate.access_resolution)
            with self.assertRaises(PermissionDenied):
                candidate.decide_access("intake.deny")
        projection = self.graphql(
            """
            query { intake_needs {
              access_verdict access_resolved_by { id } access_resolved_at access_resolution
            } }
        """,
            {},
            user=self.writer,
        )["intake_needs"]
        self.assertEqual(
            projection,
            [
                {
                    "access_verdict": None,
                    "access_resolved_by": None,
                    "access_resolved_at": None,
                    "access_resolution": None,
                }
            ],
        )
        writer.body = "Writer update"
        writer.save()
        need = self.Need._base_manager.get(pk=need.pk)
        self.assertEqual(need.access_verdict, "rejected")
        self.assertEqual(need.access_resolution, {"action": "intake.deny", "reason": "Private reason"})
        self.assertEqual(need.access_resolved_by_id, self.owner.pk)

    def test_requester_comment_filer_read_and_preview_derive_from_need(self):
        party = self.party(self.reader)
        need = self.need(party=party)
        with system_context(reason="test restricted request"):
            need.task.set_visibility("restricted")
            need.task.revoke_record_access("reader", self.reader)
        requester_task = need.task.with_actor(to_subject_ref(self.reader))
        for permission in ("read", "comment"):
            self.assertFalse(requester_task.has_access(permission))
        self.as_user(need).decide_access("intake.approve")
        for permission in ("read", "comment"):
            self.assertTrue(requester_task.has_access(permission))
        for user in (self.owner, self.writer):
            self.assertTrue(self.Party.objects.as_user(user).filter(pk=party.pk).exists())
            self.assertTrue(apps.get_model("parties", "Person").objects.as_user(user).filter(pk=party.pk).exists())
            self.assertTrue(self.reader.with_actor(to_subject_ref(user)).has_access("view_as"))

    def test_filer_name_sorts_readable_requesters_and_redacts_hidden_parties(self):
        with system_context(reason="test filer parties"):
            stranger = self.User.objects.create_user(username="request-stranger", email="stranger@example.com")
            project = apps.get_model("projects", "Project").objects.create(title="Project", owner=self.owner)
            project.grant_record_access("reader", self.writer)
            parties = {
                name: self.party(user)
                for name, user in (("Bea", self.reader), ("Al", self.owner), ("Zed", stranger), ("", self.writer))
            }
            for name, party in parties.items():
                self.Party._base_manager.filter(pk=party.pk).update(display_name=name)
            # A project request's party is not readable through a filed task.
            hidden = self.Need.objects.create(project=project, party=parties["Zed"], body="Request")
        bea = self.need(party=parties["Bea"])
        al = self.need(party=parties["Al"])
        unnamed = self.need()
        blank = self.need(party=parties[""])

        def ordered(user, direction):
            document = "{ intake_needs(order_by: [{filer_name: DIRECTION}]) { id } }".replace("DIRECTION", direction)
            return [row["id"] for row in self.graphql(document, {}, user=user)["intake_needs"]]

        def assert_ordering(user, readable, null_group):
            for direction, expected in (("asc", readable), ("desc", list(reversed(readable)))):
                ids = ordered(user, direction)
                self.assertEqual([value for value in ids if value not in null_group], expected, direction)
                positions = sorted(ids.index(value) for value in null_group)
                self.assertEqual(positions, list(range(positions[0], positions[0] + len(null_group))), direction)

        self.assertFalse(self.Party.objects.as_user(self.writer).filter(pk=parties["Zed"].pk).exists())
        assert_ordering(self.admin, [al.sqid, bea.sqid, hidden.sqid], [unnamed.sqid, blank.sqid])
        assert_ordering(self.writer, [al.sqid, bea.sqid], [unnamed.sqid, hidden.sqid, blank.sqid])
        before = {direction: ordered(self.writer, direction) for direction in ("asc", "desc")}
        with system_context(reason="test hidden filer rename"):
            self.Party._base_manager.filter(pk=parties["Zed"].pk).update(display_name="Aaron")
        for direction in ("asc", "desc"):
            self.assertEqual(ordered(self.writer, direction), before[direction])
        assert_ordering(self.admin, [hidden.sqid, al.sqid, bea.sqid], [unnamed.sqid, blank.sqid])

    def test_filer_name_places_missing_names_by_hasura_order_semantics(self):
        """Missing and empty names sort as NULL: last for ``asc``, first for
        ``desc`` (Hasura's contract, the same on every database), and an
        explicit ``*_nulls_last`` keeps them last when descending."""

        named_party = self.party(self.reader)
        blank_party = self.party(self.writer)
        with system_context(reason="test filer null placement"):
            self.Party._base_manager.filter(pk=named_party.pk).update(display_name="Named requester")
            self.Party._base_manager.filter(pk=blank_party.pk).update(display_name="")
        unnamed = self.need()
        named = self.need(party=named_party)
        blank = self.need(party=blank_party)
        expected = {
            "asc": [named.sqid, unnamed.sqid, blank.sqid],
            "desc": [unnamed.sqid, blank.sqid, named.sqid],
            "desc_nulls_last": [named.sqid, unnamed.sqid, blank.sqid],
        }
        for direction, ids in expected.items():
            with self.subTest(direction=direction):
                document = "{ intake_needs(order_by: [{filer_name: DIRECTION}]) { id } }".replace(
                    "DIRECTION", direction,
                )
                rows = self.graphql(document, {}, user=self.writer)["intake_needs"]
                self.assertEqual([row["id"] for row in rows], ids)

    def test_duplicate_refusal_returns_code_and_rolls_back_all_movers(self):
        source = self.need(party=self.party(self.reader))
        canonical = self.need()
        before_stage = source.task.stage_id
        follower_model = apps.get_model("messaging", "ThreadFollower")
        before_followers = list(follower_model._base_manager.order_by("pk").values_list("pk", "thread_id", "party_id"))
        original = self.Task.validate_record_access_subject

        def refuse_target(task, relation, subject):
            if task.pk == canonical.task_id:
                raise RecordAccessSubjectRefused()
            return original(task, relation, subject)

        with patch.object(self.Task, "validate_record_access_subject", refuse_target):
            result = self.graphql(
                """
                mutation($task: ID!, $canonical: ID!) {
                  mark_task_duplicate(task: $task, canonical: $canonical) { ok validation_errors }
                }
            """,
                {"task": source.task.sqid, "canonical": canonical.task.sqid},
            )["mark_task_duplicate"]
        self.assertEqual(result, {"ok": False, "validation_errors": {"canonical": ["RECORD_ACCESS_SUBJECT_REFUSED"]}})
        source.refresh_from_db()
        self.assertNotEqual(source.task_id, canonical.task_id)
        self.assertIsNone(source.original_task_id)
        self.assertEqual(self.Task._base_manager.get(pk=source.task_id).stage_id, before_stage)
        self.assertFalse(
            apps.get_model("projects", "TaskRelation")._base_manager.filter(task_id=source.task_id).exists()
        )
        self.assertEqual(
            list(follower_model._base_manager.order_by("pk").values_list("pk", "thread_id", "party_id")),
            before_followers,
        )

    def test_intake_permissions_compile_to_sql(self):
        permissions = [
            (self.Need, name)
            for name in (
                "read",
                "write",
                "create",
                "delete",
                "write__party",
                "read__claimed_email",
                "share",
                "read__access_decision",
            )
        ] + [
            (self.Task, "comment"),
            (self.Party, "read"),
            (apps.get_model("parties", "Person"), "read"),
            (self.User, "view_as"),
            (apps.get_model("decisions", "Decision"), "read"),
            (apps.get_model("decisions", "Decision"), "act"),
            (apps.get_model("decisions", "DecisionGroup"), "read"),
        ]
        for model, permission in permissions:
            with self.subTest(model=model, permission=permission):
                sql, _ = model.objects.with_actor(self.owner).with_action(permission).scoped().query.sql_with_params()
                self.assertIn("SELECT", sql)


class NeedResetAccessTests(IntakeAccessCase):
    def test_reset_revokes_requester_access_and_preserves_identity_password_and_history(self):
        party = self.party(self.reader)
        need = self.need(email="", party=party)
        with system_context(reason="tests.intake.requester_only"):
            need.task.set_visibility("restricted")
            need.task.revoke_record_access("reader", self.reader)
        self.as_user(need).decide_access("intake.approve")
        need.refresh_from_db()
        requester_task = self.Task.objects.as_user(self.reader).filter(pk=need.task_id)
        self.assertTrue(requester_task.exists())
        password = self.User._base_manager.get(pk=self.reader.pk).password
        previous = need.access_decision
        revision = need.revision
        with self.assertRaises(PermissionDenied):
            self.as_user(need, self.writer).reset_access(confirmed=True, expected_revision=revision)
        owner_need = self.as_user(need, self.owner)
        with self.assertRaises(ValidationError):
            owner_need.reset_access(confirmed=False, expected_revision=revision)
        with self.assertRaises(StaleRevisionError):
            owner_need.reset_access(confirmed=True, expected_revision=revision + 1)
        need.refresh_from_db()
        self.assertEqual(need.access_decision_id, previous.pk)
        result = self.graphql("""mutation($id: ID!, $revision: Int!) {
          reset_need_access(need: $id, expected_revision: $revision, confirmed: true) { ok }
        }""", {"id": need.sqid, "revision": revision}, user=self.owner)
        self.assertTrue(result["reset_need_access"]["ok"])
        need.refresh_from_db()
        previous.refresh_from_db()
        self.assertEqual(need.party_id, party.pk)
        self.assertEqual(need.access_verdict, "pending")
        self.assertEqual(need.access_decision.group.reasked_from_id, previous.group_id)
        self.assertEqual(previous.verdict, "completed")
        self.assertEqual(self.User._base_manager.get(pk=self.reader.pk).password, password)
        self.assertFalse(requester_task.exists())
        self.assertFalse(need.task.with_actor(to_subject_ref(self.reader)).has_access("comment"))
        with self.assertRaises(StaleRevisionError):
            owner_need.reset_access(confirmed=True, expected_revision=revision)


@override_settings(REBAC_LOCAL_BACKEND_STORAGE="denormalized")
class DenormalizedNeedResetAccessTests(NeedResetAccessTests):
    pass


class DecisionRecordTests(IntakeAccessCase):
    def test_intake_and_iam_authored_documents_match_console(self):
        schema = GraphQLSchemas.from_discovery().build("console")._schema
        root = Path(__file__).resolve().parents[1]
        for addon in ("intake", "iam"):
            path = root / "addons" / "angee" / addon / "web" / "src" / "documents.ts"
            document = parse("\n".join(re.findall(r"graphql\(`(.*?)`\)", path.read_text(), flags=re.S)))
            self.assertEqual(validate(schema, document), [], addon)
