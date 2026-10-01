"""Identity and mapped duplicate-pair reviews using real domain and engine owners."""

from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.base.identity import public_id_of
from angee.base.scoping import system_queryset
from angee.jobs.enqueue import celery_app
from angee.workflows.testing.drivers import decide, load_workflow, run_until, start_run

Party = apps.get_model("parties", "Party")
Address = apps.get_model("parties", "Address")
Handle = apps.get_model("parties", "Handle")
PartyHandle = apps.get_model("parties", "PartyHandle")
MergeVeto = apps.get_model("parties", "MergeVeto")
Decision = apps.get_model("decisions", "Decision")
StepRun = apps.get_model("workflows", "StepRun")
StepAttempt = apps.get_model("workflows", "StepAttempt")


class PartyWorkflowTests(TransactionTestCase):
    """Installed graphs retain typed choices and use the resolver's standing writes."""

    def setUp(self):
        """Create independent run actor and reviewer, intercepting transport only."""
        self.enterContext(patch.object(celery_app, "send_task"))
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="party review participants"):
            self.admin = get_user_model().objects.create_user(username="party-review-admin")
            grant_role(actor=self.admin, role="angee/role:admin")
            self.reviewer = get_user_model().objects.create_user(username="party-reviewer")
            self.other = get_user_model().objects.create_user(username="party-other")
        self.identity = load_workflow("angee.workflows_parties.parties_identity_review", actor=self.admin)
        self.dedupe = load_workflow("angee.workflows_parties.parties_dedupe", actor=self.admin)

    def party(self, name="Original name", *, owner=None):
        """Use normal ownership for fixtures rather than temporary review grants."""
        with actor_context(owner or self.reviewer):
            return Party.objects.create(display_name=name)

    def run_identity(self, party, *, proposed=None, actor=None, **values):
        """Start the installed one-node review through its public input contract."""
        run = start_run(self.identity, actor=actor or self.admin, input={
            "party_id": public_id_of(party), "assignee": public_id_of(self.reviewer),
            "proposed": proposed or {"name": "Reviewed name"}, "context": {"source": "directory"}, **values,
        })
        run_until(run)
        return run

    def decisions(self, run):
        """Select retained answers through the execution relationship."""
        return list(system_queryset(Decision).filter(group__step_run__run=run).order_by("pk"))

    def answer(self, run, action="apply_identity", *, values=None):
        """Exercise the real frozen form, decision transition and workflow wakeup."""
        result = decide(self.decisions(run)[0], actor=self.reviewer, action=action, values=values or {})
        self.assertEqual(result.closed_reason, "resolved")
        run_until(run)
        return result

    def test_identity_name_address_and_handle_apply_as_resolver(self):
        party = self.party()
        with actor_context(self.reviewer):
            handle = Handle.objects.create(platform="email", value="reviewed@example.test")
            link = PartyHandle.objects.create(party=party, handle=handle, source="manual")
        run = self.run_identity(party, proposed={
            "name": "Reviewed name", "address": {"street": "17 Sample Lane", "city": "Example"},
            "handle": {"party_handle_id": public_id_of(link), "evidence": "Confirmed by directory owner"},
        })
        decision = self.decisions(run)[0]
        self.assertEqual(decision.kind, "review-party-identity")
        self.assertIsNone(decision.requester_id)
        self.assertEqual(decision.basis["current"]["name"], "Original name")
        self.answer(run, values={"name_action": "replace", "address_action": "add", "handle_action": "confirm"})
        party = system_queryset(Party).get(pk=party.pk)
        self.assertEqual((run.status, run.outcome, party.display_name), ("succeeded", "applied", "Reviewed name"))
        self.assertEqual(party.updated_by_id, self.reviewer.pk)
        address = system_queryset(Address).get(party=party)
        self.assertEqual((address.street, address.label, address.created_by_id),
                         ("17 Sample Lane", "Primary", self.reviewer.pk))
        self.assertTrue(system_queryset(PartyHandle).get(pk=link.pk).is_confirmed)
        self.assertEqual(run.output["context"], {"source": "directory"})

    def test_matching_identity_skips_admission(self):
        party = self.party()
        run = self.run_identity(party, proposed={"name": party.display_name})
        self.assertEqual((run.status, run.outcome), ("succeeded", "unchanged"))
        self.assertFalse(self.decisions(run))

    def test_concurrent_identity_change_routes_conflict_without_overwrite(self):
        party = self.party()
        run = self.run_identity(party)
        with actor_context(self.reviewer):
            party.display_name = "Changed during review"
            party.save(update_fields=["display_name"])
        self.answer(run, values={"name_action": "replace"})
        self.assertEqual(run.outcome, "conflict")
        self.assertEqual(system_queryset(Party).get(pk=party.pk).display_name, "Changed during review")

    def test_reject_and_escalate_retain_reason_without_identity_write(self):
        party = self.party()
        for action, outcome in (("reject_identity", "rejected"), ("escalate_identity", "escalated")):
            with self.subTest(action=action):
                run = self.run_identity(party)
                self.answer(run, action, values={"note": "Needs another source"})
                self.assertEqual(run.outcome, outcome)
                self.assertEqual(system_queryset(Party).get(pk=party.pk).display_name, party.display_name)

    def test_non_admin_self_service_uses_existing_standing_access(self):
        party = self.party()
        with actor_context(self.admin):
            self.identity.grant_record_access("starter", self.reviewer)
        run = self.run_identity(party, actor=self.reviewer, assignee="")
        self.answer(run, values={"name_action": "replace"})
        self.assertEqual((run.status, run.outcome), ("succeeded", "applied"))

    def test_read_only_reviewer_cannot_borrow_run_actor_write(self):
        party = self.party(owner=self.other)
        with actor_context(self.admin):
            party.grant_record_access("reader", self.reviewer)
        run = self.run_identity(party)
        self.answer(run, values={"name_action": "replace"})
        self.assertEqual(run.status, "failed")
        self.assertEqual(system_queryset(Party).get(pk=party.pk).display_name, party.display_name)
        self.assertIn("PermissionDenied", system_queryset(StepAttempt).filter(step_run__run=run).last().stacktrace)

    def test_unreadable_current_evidence_is_not_disclosed_to_assignee(self):
        party = self.party()
        with actor_context(self.other):
            Address.objects.create(party=party, street="Private address")
        run = self.run_identity(party)
        self.assertEqual(run.status, "failed")
        self.assertFalse(self.decisions(run))

    def test_demo_resources_supply_browsable_identity_and_duplicate_candidates(self):
        ledger = apps.get_model("resources", "Resource").objects
        ledger.load_xref("angee.iam.user_admin", model=get_user_model(), actor=self.admin, allow_non_dev=True)
        sample, left, right = (
            ledger.load_xref(f"angee.workflows_parties.{key}", model=Party,
                             actor=self.admin, allow_non_dev=True)
            for key in ("identity_sample", "duplicate_left", "duplicate_right")
        )
        for key in ("duplicate_left_handle", "duplicate_right_handle"):
            ledger.load_xref(f"angee.workflows_parties.{key}", model=Handle,
                             actor=self.admin, allow_non_dev=True)
        self.assertEqual(sample.display_name, "Directory sample")
        with actor_context(self.admin):
            candidates = Party.objects.duplicate_candidates()
        self.assertEqual([(pair.left.pk, pair.right.pk) for pair in candidates], [(left.pk, right.pk)])

    def duplicate_pair(self, prefix):
        """Distinct native handles normalize to the same comparison identity."""
        left, right = self.party(f"{prefix} left"), self.party(f"{prefix} right")
        with actor_context(self.reviewer):
            Handle.objects.create(platform="email", value=f"{prefix}@example.test", party=left)
            Handle.objects.create(platform="email", value=f"{prefix.upper()}@example.test", party=right)
        return left, right

    def test_map_reviews_each_pair_and_composes_merge_veto_and_skip(self):
        pairs = [self.duplicate_pair(name) for name in ("alpha", "bravo", "charlie")]
        run = start_run(self.dedupe, actor=self.admin, input={"assignee": public_id_of(self.reviewer)})
        run_until(run)
        decisions = self.decisions(run)
        self.assertEqual(len(decisions), 3)
        map_step = system_queryset(StepRun).get(run=run, node_key="review_pairs")
        self.assertEqual(list(map_step.map_rows().with_actor(self.admin).order_by("map_index")
                              .values_list("map_index", flat=True)),
                         [0, 1, 2])
        for decision, action, values in zip(decisions,
                ("merge_parties", "keep_parties_separate", "skip_duplicate_pair"),
                ({"survivor": "left"}, {}, {}), strict=True):
            result = decide(decision, actor=self.reviewer, action=action, values=values)
            self.assertEqual(result.closed_reason, "resolved")
        run_until(run)
        self.assertEqual((run.status, run.outcome), ("succeeded", "done"))
        self.assertEqual([item["outcome"] for item in run.output], ["merged", "kept_separate", "skipped"])
        self.assertEqual(system_queryset(Party).get(pk=pairs[0][1].pk).merged_into_id, pairs[0][0].pk)
        self.assertTrue(MergeVeto.objects.forbids(*pairs[1]))
        with actor_context(self.reviewer):
            remaining = Party.objects.duplicate_candidates()
        self.assertEqual([(pair.left.pk, pair.right.pk) for pair in remaining], [(pairs[2][0].pk, pairs[2][1].pk)])

    def test_empty_and_non_admin_scoped_duplicate_scans(self):
        self.duplicate_pair("hidden")
        with actor_context(self.admin):
            self.dedupe.grant_record_access("starter", self.other)
        run = start_run(self.dedupe, actor=self.other, input={"assignee": public_id_of(self.other)})
        run_until(run)
        self.assertEqual((run.status, run.outcome, run.output), ("succeeded", "done", []))
        self.assertFalse(self.decisions(run))

    def test_duplicate_evidence_requires_assignee_read_on_the_supporting_handles(self):
        left, right = self.duplicate_pair("private")
        with system_context(reason="a separate owner controls the shared handle evidence"):
            Handle.objects.filter(party__in=(left, right)).update(created_by=self.other)
        run = start_run(self.dedupe, actor=self.admin, input={"assignee": public_id_of(self.reviewer)})
        run_until(run)
        self.assertEqual((run.status, run.outcome), ("failed", "error"))
        self.assertFalse(self.decisions(run))
        attempt = system_queryset(StepAttempt).filter(step_run__run=run, result="failed").get()
        self.assertIn("declared permission", attempt.error)
        self.assertEqual(Party.objects.with_actor(self.reviewer).duplicate_candidates(), [])

    def test_duplicate_merge_replay_is_idempotent_and_a_different_merge_stays_intact(self):
        for prefix, same_target in (("repeat", True), ("changed", False)):
            with self.subTest(same_target=same_target):
                left, right = self.duplicate_pair(prefix)
                run = start_run(self.dedupe, actor=self.admin, input={"assignee": public_id_of(self.reviewer)})
                run_until(run)
                decision = self.decisions(run)[0]
                survivor = left if same_target else self.party("Another survivor")
                with actor_context(self.reviewer):
                    Party.objects.merge(into=survivor, source=right, actor=self.reviewer)
                decide(decision, actor=self.reviewer, action="merge_parties", values={"survivor": "left"})
                run_until(run)
                self.assertEqual(system_queryset(Party).get(pk=right.pk).merged_into_id, survivor.pk)
                if same_target:
                    self.assertEqual((run.status, run.output[0]["output"]["result"]),
                                     ("succeeded", "already_merged"))
                else:
                    self.assertEqual(run.status, "waiting")
                    latest = self.decisions(run)[-1]
                    self.assertNotEqual(latest.group_id, decision.group_id)
                    self.assertEqual(latest.errors, {"__all__": ["Only canonical parties can be merged."]})
