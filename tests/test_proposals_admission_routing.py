"""Actual native proposal admission and immutable saves retain producer identity."""

import os
from datetime import timedelta
from pathlib import Path

import environ
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import (
    ObjectRef,
    RelationshipTuple,
    actor_context,
    database_context,
    system_context,
    to_subject_ref,
    write_relationships,
)

from angee.base.testing.packages import write_addon_distribution
from angee.provenance.context import changeset, current_changeset
from tests.composed_host import run_composed_tests


class ReceiptWriteRouter:
    def db_for_write(self, model, **hints):
        if model._meta.label_lower == "proposal_admission.receipt":
            return "producer"
        return None


class AdmissionRoutingCases(TransactionTestCase):
    __test__ = False
    databases = {"default", "producer"}
    reset_sequences = True
    maxDiff = None

    def setUp(self):
        self.Proposal = apps.get_model("proposals", "Proposal")
        self.Round = apps.get_model("proposals", "Round")
        self.Message = apps.get_model("messaging", "Message")
        self.Party = apps.get_model("parties", "Party")
        self.Answer = apps.get_model("proposals", "Answer")
        self.Receipt = apps.get_model("proposal_admission", "Receipt")
        fixtures = {}
        for alias in ("default", "producer"):
            call_command("rebac", "sync", database=alias, verbosity=0)
            with (
                database_context(alias),
                system_context(reason="tests.proposals.routing.seed"),
                changeset(fresh=True, using=alias),
            ):
                users = get_user_model().objects.db_manager(alias)
                manager = users.create_person_as_system(
                    username=f"admission-manager-{alias}",
                    email=f"admission-manager-{alias}@example.test",
                    reason="tests.proposals.routing.person",
                )
                responder = users.create_person_as_system(
                    username=f"admission-responder-{alias}",
                    email=f"admission-responder-{alias}@example.test",
                    reason="tests.proposals.routing.person",
                )
                project = (
                    apps.get_model("projects", "Project")
                    .objects.db_manager(alias)
                    .create(
                        title="Admission",
                        owner=manager,
                    )
                )
                milestone = (
                    apps.get_model("projects", "Milestone")
                    .objects.db_manager(alias)
                    .create(
                        project=project,
                        name="Questions",
                        sort_order=1024,
                    )
                )
                round = self.Round.objects.db_manager(alias).create(
                    project=project,
                    facilitator=manager,
                    name="Native admission",
                    clarifications_shared_until=milestone,
                    last_call_at=timezone.now() + timedelta(days=1),
                    submission_deadline=timezone.now() + timedelta(days=2),
                )
                apps.get_model("proposals", "Topic").objects.db_manager(alias).create(
                    round=round,
                    key="offer",
                    name="Offer",
                )
                proposal = self.Proposal.objects.db_manager(alias).create(round=round, responder=responder)
                party = apps.get_model("parties", "Person").objects.db_manager(alias).for_user(manager)
                message = self.Message.objects.db_manager(alias).create(
                    platform="email",
                    metadata={"proposal_capture": {"answers": {"offer": "Producer offer"}}},
                )
                write_relationships(
                    [
                        RelationshipTuple(
                            ObjectRef("messaging/message", str(message.pk)), "reader", to_subject_ref(manager)
                        ),
                    ]
                )
                self.Receipt.objects.db_manager(alias).create(pk=11, identity=alias, title="initial")
                fixtures[alias] = manager, round, proposal, party, message
        self.assertEqual([row.pk for row in fixtures["default"]], [row.pk for row in fixtures["producer"]])
        self.manager, self.round, self.proposal, self.party, self.message = fixtures["default"]
        self.baseline = self.snapshot("default")

    @property
    def user(self):
        return get_user_model()._base_manager.using("producer").get(pk=self.manager.pk)

    def selected(self, row, alias="producer"):
        return type(row)._base_manager.using(alias).get(pk=row.pk)

    def snapshot(self, alias):
        return {
            model._meta.label: list(model._base_manager.using(alias).order_by("pk").values())
            for model in (
                self.Round,
                self.Proposal,
                self.Answer,
                self.Message,
                self.Receipt,
                self.Receipt.history.model,
            )
        }

    def test_capture_identify_and_actual_history_close_leave_default_untouched(self):
        round, message, party = (self.selected(row) for row in (self.round, self.message, self.party))
        user = self.user
        with (
            CaptureQueriesContext(connections["default"]) as default_queries, database_context("default"),
        ):
            with actor_context(user), changeset(fresh=True, using="producer"):
                target = self.selected(self.proposal).with_actor(user)
                target.identify_party(party)
                captured = self.Proposal.objects.db_manager("producer").capture_from_message(message, round, party)
                replay = self.Proposal.objects.capture_from_message(message, round, party)
                self.assertEqual(replay.pk, captured.pk)
                receipt = self.Receipt.objects.using("producer").get(pk=11)
                receipt.title = "captured"
                receipt.save(update_fields=["title"])
                boundary = current_changeset(required=True)
            boundary.refresh_from_db(using="producer")
            self.assertEqual(boundary.state, "closed")
        self.assertEqual(list(default_queries), [])
        self.assertEqual(self.selected(self.proposal).party_id, party.pk)
        self.assertEqual(captured.state, "submitted")
        self.assertEqual(captured.submitted_by_id, user.pk)
        self.assertEqual(self.Answer._base_manager.using("producer").get(proposal=captured).body, "Producer offer")
        self.assertEqual(self.snapshot("default"), self.baseline)

    def test_mixed_identities_and_rollback_cannot_mutate_either_graph(self):
        round, message, party = (self.selected(row) for row in (self.round, self.message, self.party))
        before = self.snapshot("producer")
        with (
            CaptureQueriesContext(connections["default"]) as default_queries, database_context("default"),
        ):
            for refs in ((self.message, round, party), (message, self.round, party), (message, round, self.party)):
                with self.assertRaises(ValidationError):
                    self.Proposal.objects.db_manager("producer").capture_from_message(*refs)
            with self.assertRaises(ValidationError):
                self.selected(self.proposal).identify_party(self.party)
            with self.assertRaisesRegex(ValueError, "caller rollback"):
                with (
                    transaction.atomic(using="producer"),
                    actor_context(self.user),
                    changeset(fresh=True, using="producer"),
                ):
                    self.Proposal.objects.db_manager("producer").capture_from_message(message, round, party)
                    receipt = self.Receipt.objects.using("producer").get(pk=11)
                    receipt.title = "rolled back"
                    receipt.save(using="producer", update_fields=["title"])
                    raise ValueError("caller rollback")
        self.assertEqual(list(default_queries), [])
        self.assertEqual(self.snapshot("producer"), before)
        self.assertEqual(self.snapshot("default"), self.baseline)

    def test_immutable_guard_uses_saved_explicit_and_native_router_destinations(self):
        user = self.user
        explicit = self.Receipt.objects.using("default").get(pk=11)
        routed = self.Receipt.objects.using("default").get(pk=11)
        with (
            CaptureQueriesContext(connections["default"]) as default_queries, database_context("default"),
        ):
            with actor_context(user), changeset(fresh=True, using="producer"):
                row = self.Receipt.objects.using("producer").get(pk=11)
                row.title = "saved producer"
                row.save(update_fields=["title"])
                row.identity = "refused"
                with self.assertRaises(ValidationError):
                    row.save()
                row.allow_immutable_save("identity")
                row.identity = "allowed once"
                row.save()
                row.identity = "refused again"
                with self.assertRaises(ValidationError):
                    row.save()
            # A detached existing row can explicitly choose another actual save destination.
            explicit.identity, explicit.title = "allowed once", "explicit"
            with actor_context(user), changeset(fresh=True, using="producer"):
                explicit.save(using="producer")
            routed.identity, routed.title = "allowed once", "native router"
            with (
                override_settings(DATABASE_ROUTERS=[ReceiptWriteRouter()]),
                actor_context(user),
                changeset(fresh=True, using="producer"),
            ):
                routed.save()
        self.assertEqual(list(default_queries), [])
        final = self.Receipt.objects.using("producer").get(pk=11)
        self.assertEqual((final.identity, final.title), ("allowed once", "native router"))
        self.assertEqual(self.snapshot("default"), self.baseline)


def test_native_proposal_admission_routing(tmp_path: Path):
    addon = tmp_path / "addons" / "example" / "proposal_admission"
    addon.mkdir(parents=True)
    (addon / "__init__.py").touch()
    write_addon_distribution(addon, "example.proposal_admission", depends_on=("angee.proposals",))
    (addon / "models.py").write_text(
        "from django.db import models\n"
        "from django_provenance.history import HistoryMixin, ModelHistory\n"
        "from angee.base.models.locks import ImmutableFieldsMixin\n"
        "class Receipt(ImmutableFieldsMixin, HistoryMixin):\n"
        " identity = models.CharField(max_length=80)\n"
        " title = models.CharField(max_length=80)\n"
        ' immutable_fields = ("identity",)\n'
        " history = ModelHistory(inherit=True, use_base_model_db=True)\n"
        " class Meta(HistoryMixin.Meta):\n"
        "  abstract = False\n"
    )
    postgresql = connection.vendor == "postgresql"
    if postgresql:
        producer = environ.Env.db_url_config(os.environ["DATABASE_URL"])
        worker = os.environ.get("PYTEST_XDIST_WORKER", "")
        suffix = f"_{worker}" if worker else ""
        producer["TEST"] = {"NAME": f"test_proposals_producer_{producer['NAME']}{suffix}"}
    else:
        producer = {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    (addon / "autoconfig.py").write_text(f"SETTINGS = {{'DATABASES.producer': {producer!r}}}\n")
    run_composed_tests(
        tmp_path,
        "tests.test_proposals_admission_routing.AdmissionRoutingCases",
        app="example.proposal_admission",
        test_postgresql=postgresql,
        addon_dirs=(tmp_path / "addons",),
    )
