"""Intake contracts executed only by the isolated composed Django host."""

from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, models
from django.db.migrations.state import ProjectState
from django.test import RequestFactory, TransactionTestCase
from rebac import PermissionDenied, actor_context, system_context
from rebac.actors import is_sudo, to_subject_ref
from rebac.backends import backend
from rebac.backends.local_query import LocalQueryScope
from rebac.roles import grant as grant_role

from angee.base.errors import RecordAccessSubjectRefused
from angee.base.mixins import StaleRevisionError
from angee.graphql.schema import GraphQLSchemas
from angee.intake.runtime_migrations.need_access_decision import forwards as backfill_access
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

    def graphql(self, query, variables, *, user=None):
        user = user or self.admin
        request = RequestFactory().post("/graphql/")
        request.user = user
        with actor_context(user):
            result = (
                GraphQLSchemas.from_discovery()
                .build("public")
                .execute_sync(
                    query,
                    variable_values=variables,
                    context_value=SimpleNamespace(request=request),
                )
            )
        self.assertIsNone(result.errors, result.errors)
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
    def test_action_schema_accepts_only_declared_decisions(self):
        schema = GraphQLSchemas.from_discovery().graphql_schema("public")
        action = schema.mutation_type.fields["decide_need_access"].args["action"].type.of_type
        self.assertEqual(set(action.values), {"APPROVE", "DENY"})

    def test_deny_replay_stale_revision_then_approve_once(self):
        need = self.as_user(self.need(), self.owner)
        before = need.revision
        need.decide_access("deny", reason="Not yet", expected_revision=before)
        self.assertEqual(need.access_verdict, "rejected")
        self.assertEqual(need.access_resolution, {"action": "deny", "reason": "Not yet"})
        self.assertEqual(need.access_resolved_by_id, self.owner.pk)
        self.assertEqual(need.revision, before + 1)
        self.assertIsNotNone(need.access_resolved_at)
        need.decide_access("deny", reason="Replay")
        self.assertEqual(need.revision, before + 1)
        self.assertEqual(need.access_resolution["reason"], "Not yet")
        with self.assertRaises(PermissionDenied):
            need.decide_access("approve")
        need = self.as_user(need)
        with self.assertRaises(StaleRevisionError):
            need.decide_access("approve", expected_revision=before)
        account = need.decide_access("approve", reason="Granted", expected_revision=before + 1)
        self.assertEqual(need.revision, before + 2)
        self.assertEqual(need.access_verdict, "completed")
        self.assertEqual(need.access_resolution, {"action": "approve", "reason": "Granted"})
        self.assertFalse(account.has_usable_password())
        self.assertEqual(need.decide_access("approve").pk, account.pk)
        self.assertEqual(need.revision, before + 2)
        with self.assertRaisesMessage(ValidationError, "Approved access is final"):
            need.decide_access("deny")
        self.assertEqual(self.User._base_manager.filter(email="new@example.com").count(), 1)

    def test_existing_account_without_claimed_email_can_be_approved(self):
        need = self.as_user(self.need(email="", party=self.party(self.reader)))
        before = need.revision
        self.assertEqual(need.decide_access("approve").pk, self.reader.pk)
        self.assertEqual(need.revision, before + 1)
        self.assertEqual(need.access_verdict, "completed")

    def test_conflicts_are_in_band_and_do_not_overwrite_an_account(self):
        for email, party, message in (
            ("", None, "This request has no claimed email."),
            (self.writer.email, self.party(self.reader), "The claimed email does not name the assigned account."),
            ("unknown@example.com", self.party(self.reader), "The claimed email does not name the assigned account."),
        ):
            with self.subTest(email=email):
                need = self.need(email=email, party=party)
                result = self.graphql(
                    """
                    mutation($need: ID!) {
                      decide_need_access(need: $need, action: APPROVE) { ok validation_errors }
                    }
                """,
                    {"need": need.sqid},
                )["decide_need_access"]
                self.assertEqual(result, {"ok": False, "validation_errors": {"conflict": [message]}})
                need.refresh_from_db()
                self.assertEqual(need.party_id, party.pk if party else None)
                self.assertEqual(need.access_verdict, "pending")
        self.assertFalse(self.User._base_manager.filter(email="unknown@example.com").exists())

    def test_inactive_account_is_refused(self):
        with system_context(reason="test inactive account"):
            self.reader.is_active = False
            self.reader.save(update_fields=("is_active",))
        for party in (None, self.party(self.reader)):
            with self.subTest(party=party):
                need = self.as_user(self.need(email=self.reader.email, party=party))
                with self.assertRaisesMessage(ValidationError, "inactive"):
                    need.decide_access("approve")
                need.refresh_from_db()
                self.assertEqual(need.access_verdict, "pending")

    def test_party_change_or_clear_resets_decision_in_the_same_save(self):
        for replacement in (self.party(self.writer), None):
            for partial in (False, True):
                with self.subTest(replacement=replacement, partial=partial):
                    need = self.as_user(self.need(email="", party=self.party(self.reader)))
                    need.decide_access("approve")
                    before = need.revision
                    need.party = replacement
                    need.save(**({"update_fields": ("party",)} if partial else {}))
                    need.refresh_from_db()
                    self.assertEqual(need.revision, before + 1)
                    self.assertEqual(need.access_verdict, "pending")
                    self.assertIsNone(need.access_resolved_by_id)
                    self.assertIsNone(need.access_resolved_at)
                    self.assertEqual(need.access_resolution, {})

    def test_unchanged_party_and_unpersisted_assignment_keep_decision(self):
        need = self.as_user(self.need(email="", party=self.party(self.reader)))
        need.decide_access("approve")
        need.save()
        need.party = self.party(self.writer)
        need.body = "Edited request"
        need.save(update_fields=("body",))
        need.refresh_from_db()
        self.assertEqual(need.access_verdict, "completed")
        self.assertEqual(need.party_id, self.party(self.reader).pk)

    def test_reconcile_never_yields_system_context_and_clears_old_decision(self):
        need = self.legacy_need()
        with system_context(reason="test legacy decision"):
            models.QuerySet.update(
                self.Need._base_manager.filter(pk=need.pk),
                access_verdict="completed",
                access_resolution={"action": "approve", "reason": ""},
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

    def test_historical_backfill_approves_only_accounts_reconciliation_keeps(self):
        unconfirmed = self.legacy_need()
        confirmed = self.legacy_need(confirmed=True)
        edited = self.legacy_need(touched=True)
        manual = self.need(email="", party=self.party(self.reader))
        historical = ProjectState.from_apps(apps).apps
        with connection.schema_editor() as editor:
            backfill_access(historical, editor)
            backfill_access(historical, editor)
        for need, verdict in (
            (unconfirmed, "pending"),
            (confirmed, "completed"),
            (edited, "completed"),
            (manual, "completed"),
        ):
            need.refresh_from_db()
            self.assertEqual(need.access_verdict, verdict)
            self.assertIsNone(need.access_resolved_by_id)
            self.assertIsNone(need.access_resolved_at)


class NeedAccessTests(IntakeAccessCase):
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
                self.as_user(need).decide_access("approve")
        self.assertFalse(self.User._base_manager.filter(email="new@example.com").exists())
        need.refresh_from_db()
        self.assertIsNone(need.party_id)
        self.assertEqual(need.access_verdict, "pending")

    def test_field_redaction_does_not_erase_decisions_on_writer_save(self):
        need = self.as_user(self.need(), self.owner)
        need.decide_access("deny", reason="Private reason")
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
                candidate.decide_access("deny")
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
        self.assertEqual(need.access_resolution, {"action": "deny", "reason": "Private reason"})
        self.assertEqual(need.access_resolved_by_id, self.owner.pk)

    def test_requester_comment_filer_read_and_preview_derive_from_need(self):
        party = self.party(self.reader)
        need = self.need(party=party)
        with system_context(reason="test restricted request"):
            need.task.set_visibility("restricted")
            need.task.revoke_record_access("reader", self.reader)
        self.assertTrue(need.task.with_actor(to_subject_ref(self.reader)).has_access("comment"))
        self.assertFalse(need.task.has_access("read"))
        for user in (self.owner, self.writer):
            self.assertTrue(self.Party.objects.as_user(user).filter(pk=party.pk).exists())
            self.assertTrue(apps.get_model("parties", "Person").objects.as_user(user).filter(pk=party.pk).exists())
            self.assertTrue(self.reader.with_actor(to_subject_ref(user)).has_access("view_as"))

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
        scope = LocalQueryScope(backend(), to_subject_ref(self.owner), "default")
        permissions = [
            (self.Need, name)
            for name in (
                "read",
                "write",
                "create",
                "delete",
                "write__party",
                "read__claimed_email",
                "read__access_verdict",
                "read__access_resolved_by",
                "read__access_resolved_at",
                "read__access_resolution",
            )
        ] + [
            (self.Task, "comment"),
            (self.Party, "read"),
            (apps.get_model("parties", "Person"), "read"),
            (self.User, "view_as"),
        ]
        for model, permission in permissions:
            with self.subTest(model=model, permission=permission):
                predicate = scope.predicate(model, permission, model._meta.rebac_resource_type)
                sql, _ = model._base_manager.filter(predicate).query.sql_with_params()
                self.assertIn("SELECT", sql)
