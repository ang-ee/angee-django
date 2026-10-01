"""Additional intake scenarios on the shared emitted-host fixtures."""

from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.state import ProjectState
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context, system_context

from angee.base.errors import RecordAccessSubjectRefused
from angee.intake.runtime_migrations.need_access_decision import forwards
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from tests.native_intake_capture import ChannelIntakeCaptureTests, IntakeAccessCase


class IntakeCampaign(IntakeAccessCase):
    """Existing Need/account composition; only test identities and store vary."""

    storage = "registry"

    def setUp(self):
        setting = override_settings(REBAC_LOCAL_BACKEND_STORAGE=self.storage, REBAC_SUPERUSER_BYPASS=False)
        setting.enable()
        self.addCleanup(setting.disable)
        super().setUp()
        with system_context(reason="tests.t3.intake_names"):
            for role in ("owner", "writer", "reader", "admin"):
                user = getattr(self, role)
                user.username = f"{self._testMethodName}-{role}"
                user.email = f"{self._testMethodName}-{role}@example.test"
                user.save(update_fields=("username", "email"))

    def tuples(self):
        return tuple(
            list(apps.get_model("rebac", name)._base_manager.order_by("pk").values())
            for name in ("Relationship", "RelationshipRegistry")
        )

    def test_denial_supersession_retains_answer_and_approval_is_final(self):
        need = self.as_user(self.need(email="", party=self.party(self.reader)))
        before = self.tuples()
        need.decide_access("deny", reason="First answer")
        denied = need.access_decision
        receipt = (denied.resolution, denied.resolved_by_id, denied.resolved_at)
        self.assertEqual(denied.verdict, "rejected")
        need.decide_access("approve", reason="Reconsidered")
        self.assertEqual(need.access_verdict, "completed")
        self.assertNotEqual(denied.pk, need.access_decision_id)
        denied.refresh_from_db()
        self.assertEqual((denied.resolution, denied.resolved_by_id, denied.resolved_at), receipt)
        self.assertEqual(denied.superseded_by_id, need.access_decision_id)
        approved_id, revision = need.access_decision_id, need.revision
        self.assertEqual(need.decide_access("approve").pk, self.reader.pk)
        self.assertEqual((need.access_decision_id, need.revision), (approved_id, revision))
        with self.assertRaises(ValidationError):
            need.decide_access("deny")
        self.assertEqual(self.tuples(), before)

    def test_pending_approval_uses_assigned_party_even_when_email_names_another_account(self):
        need = self.as_user(self.need(email=self.writer.email, party=self.party(self.reader)))
        self.assertEqual(need.access_verdict, "pending")
        before = self.tuples()
        linked = need.decide_access("approve")
        self.assertEqual(linked.pk, self.reader.pk)
        self.assertEqual(need.access_verdict, "completed")
        self.assertEqual(need.party_id, self.party(self.reader).pk)
        self.assertEqual(self.tuples(), before)

    def test_party_replacement_and_clear_supersede_without_rewriting_prior_approval(self):
        need = self.as_user(self.need(email="", party=self.party(self.reader)))
        need.decide_access("approve")
        for replacement in (self.party(self.writer), None):
            previous = need.access_decision
            resolution = previous.resolution
            need.party = replacement
            need.save(update_fields=("party",))
            need.refresh_from_db()
            previous.refresh_from_db()
            self.assertEqual(need.access_verdict, "pending")
            self.assertEqual(previous.superseded_by_id, need.access_decision_id)
            self.assertEqual(previous.resolution, resolution)
            self.assertIsNone(need.access_resolved_at)

    def test_only_current_sharers_read_decisions_and_list_projections_batch_once(self):
        rows = [self.need() for _ in range(6)]
        seats = apps.get_model("decisions", "Decision")
        for user, allowed in ((self.owner, True), (self.writer, False), (self.reader, False), (self.admin, True)):
            with self.subTest(user=user.username):
                self.assertEqual(seats.objects.as_user(user).filter(pk=rows[0].access_decision_id).exists(), allowed)
                query = "{ intake_needs { access_verdict access_resolution access_resolved_at } }"
                self.graphql(query, {}, user=user)
                with CaptureQueriesContext(connection) as queries:
                    data = self.graphql(query, {}, user=user)["intake_needs"]
                self.assertEqual(len(data), 6)
                self.assertTrue(all(row["access_verdict"] == ("PENDING" if allowed else None) for row in data))
                if allowed:
                    reads = [q for q in queries if f'FROM "{seats._meta.db_table}"' in q["sql"]]
                    self.assertEqual(len(reads), 1)

    def test_refused_party_assignment_rolls_back_seat_and_follower_changes(self):
        need = self.need()
        before = (need.party_id, need.access_decision_id, need.revision)
        followers = apps.get_model("messaging", "ThreadFollower")
        follower_ids = set(followers._base_manager.values_list("pk", flat=True))
        party = self.party(self.reader)
        with patch.object(self.Task, "validate_record_access_subject", side_effect=RecordAccessSubjectRefused()):
            for partial in (True, False):
                with self.subTest(partial=partial), system_context(reason="tests.t3.refusal"):
                    candidate = self.Need._base_manager.get(pk=need.pk)
                    candidate.party = party
                    with self.assertRaises(RecordAccessSubjectRefused):
                        candidate.save(**({"update_fields": ("party",)} if partial else {}))
            with self.assertRaises(RecordAccessSubjectRefused):
                self.as_user(need).decide_access("approve")
        need.refresh_from_db()
        self.assertEqual((need.party_id, need.access_decision_id, need.revision), before)
        self.assertEqual(set(followers._base_manager.values_list("pk", flat=True)), follower_ids)

    def test_project_reader_loses_restricted_task_need_but_keeps_direct_need(self):
        with system_context(reason="tests.t3.need_targets"):
            project = apps.get_model("projects", "Project").objects.create(title="Project", owner=self.owner)
            project.grant_record_access("reader", self.reader)
            task = self.Task.objects.create(title="Task", project=project, owner=self.owner)
            task_need = self.Need.objects.create(task=task, body="Task request")
            direct = self.Need.objects.create(project=project, body="Project request")
        self.assertIsNone(task_need.project_id)
        with actor_context(self.reader):
            self.assertEqual(
                set(self.Need.objects.for_project(project).values_list("pk", flat=True)), {task_need.pk, direct.pk}
            )
        with actor_context(self.owner):
            task.with_actor(self.owner).set_visibility("restricted")
        with actor_context(self.reader):
            self.assertEqual(list(self.Need.objects.for_project(project).values_list("pk", flat=True)), [direct.pk])

    def test_two_requesters_comment_without_writing_and_filer_identity_revokes_with_task(self):
        """D1 grants requester comment only after an approved access decision."""
        first = self.need(party=self.party(self.reader))
        with system_context(reason="tests.t3.second_requester"):
            second_user = self.User.objects.create_user(username="second-requester", email="second@example.test")
            second = self.Need.objects.create(task=first.task, party=self.party(second_user), body="Another request")
            first.task.set_visibility("restricted")
            for user in (self.reader, second_user):
                first.task.revoke_record_access("reader", user)
        for user in (self.reader, second_user):
            self.assertFalse(first.task.with_actor(user).has_access("comment"))
        self.as_user(first).decide_access("approve")
        self.as_user(second).decide_access("approve")
        for user in (self.reader, second_user):
            self.assertTrue(first.task.with_actor(user).has_access("comment"))
        self.assertFalse(first.task.with_actor(self.reader).has_access("write"))
        current = self.as_user(first)
        current.reset_access(confirmed=True, expected_revision=current.revision)
        self.assertFalse(self.Task.objects.as_user(self.reader).filter(pk=first.task_id).exists())
        self.assertFalse(self.Party.objects.as_user(self.reader).filter(pk=second.party_id).exists())
        with actor_context(self.reader), self.assertRaises(PermissionDenied):
            first.task.with_actor(self.reader).message_post("Unusable comment arm")

    def test_reconciliation_dry_run_and_apply_clear_only_untouched_unconfirmed_party(self):
        eligible = self.legacy_need()
        confirmed = self.legacy_need(confirmed=True)
        edited = self.legacy_need(touched=True)
        output = StringIO()
        call_command("intake_reconcile_parties", stdout=output)
        eligible.refresh_from_db()
        self.assertIsNotNone(eligible.party_id)
        call_command("intake_reconcile_parties", "--apply", stdout=StringIO())
        for row in (eligible, confirmed, edited):
            row.refresh_from_db()
            self.assertEqual(row.party_id is None, row.pk == eligible.pk)

    def test_historical_migration_creates_imported_and_pending_seats_idempotently(self):
        # Legacy requests predate automatic seat admission.
        with patch.object(self.Need, "_new_access_decision", return_value=None):
            linked = self.need(email="", party=self.party(self.reader))
            waiting = self.need(email="")
        historical = ProjectState.from_apps(apps).apps
        seats = historical.get_model("decisions", "Decision")
        before = self.tuples()
        with connection.schema_editor() as editor:
            forwards(historical, editor)
            count = seats._base_manager.count()
            forwards(historical, editor)
        self.assertEqual(seats._base_manager.count(), count)
        self.assertEqual(self.tuples(), before)
        for row, verdict in ((linked, "completed"), (waiting, "pending")):
            row.refresh_from_db()
            self.assertEqual(row.access_verdict, verdict)
            self.assertIsNone(row.access_resolved_at)
            self.assertIsNone(row.access_resolved_by_id)
            self.assertEqual(row.access_decision.intake_need_id, row.pk)
            self.assertEqual(row.access_decision.group.policy, "first")


class IntakeDenormalizedCampaign(IntakeCampaign):
    storage = "denormalized"


class CaptureCampaign(TransactionTestCase):
    """Reuse the native capture fixture's setup and webform constructor."""

    storage = "registry"
    webform = ChannelIntakeCaptureTests.webform

    def setUp(self):
        setting = override_settings(REBAC_LOCAL_BACKEND_STORAGE=self.storage, REBAC_SUPERUSER_BYPASS=False)
        setting.enable()
        self.addCleanup(setting.disable)
        ChannelIntakeCaptureTests.setUp(self)
        with system_context(reason="tests.t3.capture_identity"):
            self.actor.username = self._testMethodName
            self.actor.email = f"{self._testMethodName}@example.test"
            self.actor.save(update_fields=("username", "email"))

    def test_map_validation_rejects_undeclared_email_duplicate_and_wrong_kind_properties(self):
        channel = self.webform()
        channel.form_schema["properties"]["locked"] = {"type": "string", "readOnly": True}
        for mapping in (
            {"owner": "name"},
            {"title": "absent"},
            {"title": "email"},
            {"title": "name", "note": "name"},
            {"estimate": "subject"},
            {"claimed_name": "size"},
            {"title": "locked"},
        ):
            channel.intake_field_map = mapping
            with self.subTest(mapping=mapping), system_context(reason="tests.t3.map_validation"):
                with self.assertRaises(ValidationError):
                    channel.clean()

    def test_mapping_keeps_valid_fields_when_other_answers_fail_native_coercion(self):
        channel = self.webform()
        channel.intake_field_map = {
            "title": "subject",
            "claimed_name": "name",
            "estimate": "size",
            "due_date": "day",
            "priority": "urgency",
        }
        message = self.Message(
            metadata={
                "webform": {
                    "answers": {
                        "subject": "x" * 2000,
                        "name": "Retained name",
                        "size": "not a number",
                        "day": "not a date",
                        "urgency": "not a priority",
                    }
                }
            }
        )
        self.assertEqual(channel.mapped_task_values(message), {"claimed_name": "Retained name"})

    def test_domain_configuration_requires_account_creation_authority(self):
        channel = self.webform()
        with actor_context(self.actor):
            candidate = type(channel)._base_manager.get(pk=channel.pk).with_actor(self.actor)
            self.assertTrue(candidate.has_access("write"))
            candidate.intake_requester_domains = ["example.test"]
            with self.assertRaises(PermissionDenied):
                candidate.save(update_fields=("intake_requester_domains",))
        self.assertEqual(type(channel)._base_manager.get(pk=channel.pk).intake_requester_domains, [])

    def test_existing_inactive_unlisted_and_factory_refusal_preserve_capture(self):
        channel = self.webform(domains=("example.test",))
        users = apps.get_model("iam", "User")
        for index, case in enumerate(("existing", "inactive", "unlisted", "factory_refusal")):
            domain = "elsewhere.test" if case in {"existing", "unlisted"} else "example.test"
            email = f"{case}-{self._testMethodName}@{domain}"
            existing = None
            if case in {"existing", "inactive"}:
                existing = users.objects.create_person_as_system(
                    username=f"{self._testMethodName}-{case}",
                    email=email,
                    reason="tests.t3.existing_account",
                )
                if case == "inactive":
                    with system_context(reason="tests.t3.inactive_account"):
                        users._base_manager.filter(pk=existing.pk).update(is_active=False)
            parsed = channel.webform_message(
                submission_id=f"domain-{index}",
                answers={"subject": "Retained request", "email": email},
            )
            with self.subTest(case=case), system_context(reason="tests.t3.domain_capture"):
                if case == "factory_refusal":
                    with patch.object(
                        type(users.objects), "create_person_as_system", side_effect=ValidationError("Refused")
                    ):
                        (message,) = self.Message.objects.ingest([parsed], channel=channel)
                else:
                    (message,) = self.Message.objects.ingest([parsed], channel=channel)
                need = self.Need._base_manager.get(source_message=message)
                self.assertIsNotNone(need.task_id)
                self.assertEqual(need.access_verdict, "pending")
                self.assertEqual(need.party_id is not None, case == "existing")
                self.assertEqual(users._base_manager.filter(email=email).count(), int(existing is not None))

    def test_webform_capture_creates_unusable_account_and_reuses_it_without_roster_or_grants(self):
        channel = self.webform(domains=("example.test",))
        email = f"claim-{self._testMethodName}@example.test"
        stores = [apps.get_model("rebac", name) for name in ("Relationship", "RelationshipRegistry")]
        before = [list(store._base_manager.order_by("pk").values()) for store in stores]
        for key in ("first", "first", "second"):
            parsed = channel.webform_message(
                submission_id=key,
                answers={"name": "Request author", "subject": "Printer", "size": 4, "email": email},
            )
            with system_context(reason="tests.t3.webform"):
                self.Message.objects.ingest([parsed], channel=channel)
        needs = list(self.Need._base_manager.order_by("pk"))
        self.assertEqual(len(needs), 2)
        self.assertEqual(needs[0].party_id, needs[1].party_id)
        user = apps.get_model("iam", "User")._base_manager.get(email=email)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(
            apps.get_model("spaces", "Membership")._base_manager.filter(party_id=needs[0].party_id).exists()
        )
        for need in needs:
            self.assertIsNone(need.task.owner_id)
            self.assertIsNone(need.project_id)
            self.assertEqual(need.task.estimate, 4)
            self.assertEqual(need.claimed_name, "Request author")
            self.assertEqual(need.access_verdict, "pending")
        self.assertEqual([list(store._base_manager.order_by("pk").values()) for store in stores], before)

    def test_email_capture_requires_confirmed_sender_link_and_replays_once(self):
        email = f"sender-{self._testMethodName}@example.test"
        with system_context(reason="tests.t3.email_setup"):
            self.channel.intake_queue = self.queue
            self.channel.save(update_fields=("intake_queue",))
            party = apps.get_model("parties", "Party").objects.for_user(self.actor)
            handle = apps.get_model("parties", "Handle").objects.upsert(platform="email", value=email)
            links = apps.get_model("parties", "PartyHandle")
            links.objects.link(party, handle, is_confirmed=False)
        for key, confirmed in (("suggested", False), ("confirmed", True)):
            with system_context(reason="tests.t3.email"):
                if confirmed:
                    links.objects.link(party, handle, is_confirmed=True)
                parsed = ParsedMessage(
                    external_id=key,
                    platform="email",
                    subject="Request",
                    sender=ParsedHandle(platform="email", value=email),
                    body=ParsedPart(type="text/plain", role="body", text="Please investigate"),
                )
                for _ in range(2):
                    (message,) = self.Message.objects.ingest([parsed], channel=self.channel)
                need = self.Need._base_manager.get(source_message=message)
            self.assertEqual(need.party_id, party.pk if confirmed else None)
        self.assertEqual(self.Need._base_manager.count(), 2)


class CaptureDenormalizedCampaign(CaptureCampaign):
    storage = "denormalized"
