"""Lifecycle receipts, revision checks, provisioning and question replay contracts."""

from __future__ import annotations

from contextlib import nullcontext
from copy import copy
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context, current_actor, sudo, system_context, to_subject_ref
from rebac.models import PermissionAuditEvent, active_relationship_model
from strawberry import Schema

from angee.base.models.locks import StaleRevisionError
from angee.base.testing.queries import statement_startswith
from angee.graphql.deletion import DeletePreview
from angee.messaging.testing.models import Person
from angee.projects.testing.models import Milestone, Project, ProjectBinding, Task
from angee.provenance.contrib.angee.exceptions import CreationKeyConflict
from tests.conftest import Drive
from tests.proposals_campaign import ProposalCampaign, as_actor, grant
from tests.proposals_campaign import campaign as campaign
from tests.proposals_campaign import campaign_schema as campaign_schema
from tests.proposals_campaign import proposal_delete_schema as proposal_delete_schema
from tests.proposals_models import Proposal, Round, Topic
from tests.t3_campaign import relationship_snapshot


@pytest.mark.parametrize("touched", (False, True))
def test_hasura_proposal_delete_preserves_domain_refusals(
    campaign: ProposalCampaign, proposal_delete_schema: Schema, touched: bool,
) -> None:
    """The real confirmed-delete envelope allows drafts and reports retained work."""

    proposal = campaign.admit(campaign.round(), "responder")
    manager = campaign.person("facilitator")
    if touched:
        with actor_context(manager):
            row = as_actor(proposal, manager)
            row.statement = "Retained commitment"
            row.save(update_fields=("statement",))
    with actor_context(manager):
        result = proposal_delete_schema.execute_sync(
            "mutation($id: String!) { delete_proposals_by_pk(id: $id) { id } }",
            variable_values={"id": proposal.public_id},
            context_value=SimpleNamespace(request=SimpleNamespace(user=manager)),
        )
    if touched:
        assert result.errors is not None and len(result.errors) == 1
        assert result.errors[0].message == "Only an untouched draft proposal can be deleted."
        assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}
    else:
        assert result.errors is None
        assert result.data == {"delete_proposals_by_pk": {"id": proposal.public_id}}
    assert Proposal._base_manager.filter(pk=proposal.pk).exists() is touched


@pytest.mark.parametrize("verb", ("submit", "create_track"))
@pytest.mark.parametrize("elevation", ("system", "sudo", "instance"))
def test_backed_edge_verbs_preserve_actorless_bypasses(
    campaign: ProposalCampaign,
    elevation: str,
    verb: str,
) -> None:
    """Trusted actorless callers retain receipts and idempotent track creation."""
    c = campaign
    proposal = c.admit(c.round(), "responder")
    proposal = Proposal._base_manager.get(pk=proposal.pk)
    assert current_actor() is None and proposal.actor() is None
    if elevation == "instance":
        proposal.sudo(reason="tests.proposals.campaign.actorless")
    context = {
        "system": system_context(reason="tests.proposals.campaign.actorless"),
        "sudo": sudo(reason="tests.proposals.campaign.actorless"),
        "instance": nullcontext(),
    }[elevation]
    with context:
        first = getattr(proposal, verb)()
        stored = Proposal._base_manager.get(pk=proposal.pk)
        second = getattr(proposal, verb)()
        assert second.pk == first.pk
        assert Proposal._base_manager.get(pk=proposal.pk).revision == stored.revision
    assert proposal.is_sudo() is (elevation == "instance")
    assert proposal.actor() is None
    if verb == "submit":
        assert stored.state == "submitted" and stored.submitted_at == c.now
        assert stored.submitted_by_id == proposal.responder_id
    else:
        assert stored.track_id == first.pk
        assert first.owner_id is None and first.owns_items and not first.is_sudo()
        assert Drive._base_manager.filter(project_bindings__project=first).count() == 1


@pytest.mark.parametrize("verb", ("submit", "create_track"))
@pytest.mark.parametrize("fail_write", (False, True))
def test_backed_edge_verbs_keep_pinned_attribution_and_clear_locked_sudo(
    campaign: ProposalCampaign,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    fail_write: bool,
) -> None:
    """The authorized proposal write may cross its edge and always drops sudo."""
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder")
    responder, ambient = c.person("responder"), c.person("outsider")
    proposal = as_actor(proposal, responder)
    assert not as_actor(round, responder).has_access("write")
    saved: list[Proposal] = []
    save = Proposal.save

    def observe_save(instance: Proposal, *args: Any, **kwargs: Any) -> Any:
        if instance.pk == proposal.pk:
            saved.append(instance)
            assert instance.is_sudo()
            if verb == "create_track":
                assert not as_actor(instance.track, responder).has_access("write")
            if fail_write:
                raise ValidationError("Simulated backed-edge persistence failure")
        return save(instance, *args, **kwargs)

    monkeypatch.setattr(Proposal, "save", observe_save)
    with actor_context(ambient):
        with pytest.raises(ValidationError, match="Simulated backed-edge") if fail_write else nullcontext():
            getattr(proposal, verb)()
        assert current_actor() == to_subject_ref(ambient)
    assert len(saved) == 1
    assert not saved[0].is_sudo() and saved[0].actor() == to_subject_ref(responder)
    assert not proposal.is_sudo()
    stored = Proposal._base_manager.get(pk=proposal.pk)
    if fail_write:
        assert stored.state == "draft" and stored.submitted_at is None and stored.track_id is None
    else:
        assert stored.updated_by_id == responder.pk
        if verb == "submit":
            assert stored.submitted_by_id == responder.pk
        reason = "proposals.proposal.submit" if verb == "submit" else "proposals.proposal.create_track.link"
        actor = to_subject_ref(responder)
        assert PermissionAuditEvent.objects.filter(
            kind=PermissionAuditEvent.KIND_SUDO_BYPASS,
            reason=reason,
            actor_subject_type=actor.subject_type,
            actor_subject_id=actor.subject_id,
        ).exists()


@pytest.mark.parametrize("policy", ("facilitator_only", "answers", "answers_and_tracks", "drafts_and_tracks"))
def test_opening_stamps_only_eligible_receipts_and_preserves_manual_shares(
    campaign: ProposalCampaign,
    policy: str,
) -> None:
    c = campaign
    round = c.round(policy=policy)
    submitted = c.admit(round, "submitted", track=True)
    draft = c.admit(round, "draft", track=True)
    withdrawn = c.admit(round, "withdrawn", track=True)
    retired = c.admit(round, "retired", track=True)
    manager = c.person("facilitator")
    grant(draft, "reader", c.person("reader"))
    with actor_context(manager):
        for proposal in (submitted, withdrawn, retired):
            as_actor(proposal, manager).submit()
        as_actor(withdrawn, manager).withdraw()
        as_actor(round, manager).remove_responder(c.person("retired"))
    tuples = list(active_relationship_model().objects.values_list("pk", flat=True))
    opened = c.open(round)
    assert opened.opened_at == c.now
    assert opened.opened_by_id == manager.pk
    for proposal, eligible in (
        (submitted, policy != "facilitator_only"),
        (draft, policy == "drafts_and_tracks"),
        (withdrawn, False),
        (retired, False),
    ):
        stored = Proposal._base_manager.get(pk=proposal.pk)
        assert (stored.disclosed_at is not None) is eligible
        assert (stored.track_published_at is not None) is (
            eligible and policy in {"answers_and_tracks", "drafts_and_tracks"}
        )
    assert list(active_relationship_model().objects.values_list("pk", flat=True)) == tuples
    assert as_actor(draft, c.person("reader")).has_access("read")
    with actor_context(manager):
        replay = as_actor(round, manager).open(expected_revision=opened.revision)
    assert (replay.opened_at, replay.revision) == (opened.opened_at, opened.revision)


@pytest.mark.parametrize("terminal", ("close", "cancel"))
def test_terminal_round_freezes_drafts_but_keeps_unretired_responders(
    campaign: ProposalCampaign,
    terminal: str,
) -> None:
    c = campaign
    round = c.round()
    draft = c.admit(round, "responder")
    submitted = c.admit(round, "submitted")
    c.admit(round, "retired")
    manager = c.person("facilitator")
    with actor_context(manager):
        as_actor(submitted, manager).submit()
        as_actor(round, manager).remove_responder(c.person("retired"))
        opened = as_actor(round, manager).open()
        closed = opened.close("no_award") if terminal == "close" else opened.cancel()
    assert closed.closed_at == c.now
    assert closed.closed_by_id == manager.pk
    assert set(closed.current_responders().values_list("pk", flat=True)) == {
        c.person("responder").pk,
        c.person("submitted").pk,
    }
    assert not as_actor(draft, c.person("responder")).has_access("write")
    with actor_context(c.person("responder")), pytest.raises(PermissionDenied):
        as_actor(draft, c.person("responder")).submit()
    with actor_context(manager), pytest.raises(ValidationError, match="terminal"):
        as_actor(round, manager).ask("Too late", "No new questions", recipient="responders")


def test_awards_are_exact_replay_safe_and_refuse_drafts_or_foreign_selections(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    accepted, partial, declined, draft = (c.admit(round, seat) for seat in ("accepted", "partial", "declined", "draft"))
    foreign = c.admit(c.round(), "foreign")
    manager = c.person("facilitator")
    with actor_context(manager):
        for proposal in (accepted, partial, declined):
            as_actor(proposal, manager).submit()
        opened = as_actor(round, manager).open()
        for selections in ((draft,), (foreign,)):
            with pytest.raises(ValidationError):
                as_actor(round, manager).close("awarded", accepted=selections)
        with pytest.raises(ValidationError, match="disjoint"):
            as_actor(round, manager).close("awarded", accepted=(accepted,), partial=(accepted,))
        closed = opened.close("awarded", accepted=(accepted,), partial=(partial,), expected_revision=opened.revision)
        replay = as_actor(round, manager).close("awarded", accepted=(accepted,), partial=(partial,))
        assert replay.revision == closed.revision
        with pytest.raises(ValidationError):
            as_actor(round, manager).close("awarded", accepted=(partial,))
    assert list(Proposal._base_manager.filter(round=round).order_by("pk").values_list("state", flat=True)) == [
        "accepted",
        "partially_accepted",
        "declined",
        "draft",
    ]


def test_opening_policy_widens_monotonically_without_rewriting_receipts(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round(policy="facilitator_only")
    submitted = c.admit(round, "submitted", track=True)
    draft = c.admit(round, "draft", track=True)
    manager = c.person("facilitator")
    with actor_context(manager):
        as_actor(submitted, manager).submit()
        with pytest.raises(ValidationError, match="opened"):
            as_actor(round, manager).widen_opening_policy("answers")
        opened = as_actor(round, manager).open()
        for policy in ("answers", "answers_and_tracks", "drafts_and_tracks"):
            before = opened.revision
            opened.widen_opening_policy(policy, expected_revision=before)
            assert opened.revision > before
            with pytest.raises(StaleRevisionError):
                as_actor(round, manager).widen_opening_policy(policy, expected_revision=before)
            assert Proposal._base_manager.get(pk=submitted.pk).disclosed_at == c.now
        with pytest.raises(ValidationError, match="widened"):
            opened.widen_opening_policy("answers")
    assert Proposal._base_manager.get(pk=draft.pk).disclosed_at == c.now
    assert Proposal._base_manager.get(pk=draft.pk).track_published_at == c.now


@pytest.mark.parametrize("verb", ("open", "close", "cancel", "remove_responder", "transfer_facilitation"))
def test_round_verbs_reject_stale_revisions_before_changing_any_row(campaign: ProposalCampaign, verb: str) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    manager = c.person("facilitator")
    if verb == "close":
        round = c.open(round)
    with actor_context(manager):
        current = as_actor(round, manager)
        stale = current.revision
        current.name = "Changed since the page was read"
        current.save(update_fields=("name",))
        before = Round._base_manager.filter(pk=round.pk).values().get()
        args = {
            "close": ("no_award",),
            "remove_responder": (c.person("responder"),),
            "transfer_facilitation": (c.person("next-manager"),),
        }.get(verb, ())
        with pytest.raises(StaleRevisionError) as error:
            getattr(as_actor(round, manager), verb)(*args, expected_revision=stale)
        assert error.value.code == "STALE_REVISION"
    assert Round._base_manager.filter(pk=round.pk).values().get() == before
    assert Proposal._base_manager.get(round=round).retired_at is None


@pytest.mark.parametrize("verb", ("submit", "withdraw", "publish_track"))
def test_proposal_verbs_reject_stale_revisions(campaign: ProposalCampaign, verb: str) -> None:
    c = campaign
    proposal = c.admit(c.round(), "responder", track=True)
    manager = c.person("facilitator")
    with actor_context(manager):
        if verb == "withdraw":
            as_actor(proposal, manager).submit()
        current = as_actor(proposal, manager)
        stale = current.revision
        current.statement = "The page is out of date"
        current.save(update_fields=("statement",))
        before = Proposal._base_manager.filter(pk=proposal.pk).values().get()
        with pytest.raises(StaleRevisionError):
            getattr(as_actor(proposal, manager), verb)(expected_revision=stale)
    assert Proposal._base_manager.filter(pk=proposal.pk).values().get() == before


def test_answer_audience_and_share_revisions_bump_once_and_reject_stale_replays(campaign: ProposalCampaign) -> None:
    c = campaign
    answer = c.answer(c.admit(c.round(), "responder"))
    manager = c.person("facilitator")
    with actor_context(manager):
        for verb, value in (("set_visibility", "responder"), ("set_responder_share", True)):
            current = as_actor(answer, manager)
            old = current.revision
            changed = getattr(current, verb)(value, expected_revision=old)
            assert changed.revision == old + 1
            with pytest.raises(StaleRevisionError):
                getattr(as_actor(answer, manager), verb)(value, expected_revision=old)
            replay = getattr(as_actor(answer, manager), verb)(value, expected_revision=changed.revision)
            assert replay.revision == changed.revision


def test_task_responder_share_locks_revisions_and_refuses_tasks_outside_tracks(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder", track=True)
    with system_context(reason="tests.proposals.campaign.share_revision"):
        task = Task.objects.create(project=proposal.track, title="Shared detail")
        ordinary = Task.objects.create(project=round.project, title="Ordinary work")
    manager = c.person("facilitator")
    with actor_context(manager):
        old = task.revision
        changed = as_actor(task, manager).set_responder_share(True, expected_revision=old)
        assert changed.revision == old + 1
        with pytest.raises(StaleRevisionError):
            as_actor(task, manager).set_responder_share(False, expected_revision=old)
        replay = as_actor(task, manager).set_responder_share(True, expected_revision=changed.revision)
        assert replay.revision == changed.revision
    with actor_context(c.person("project-owner")), pytest.raises(ValidationError, match="track"):
        as_actor(ordinary, c.person("project-owner")).set_responder_share(True)


def test_provisioning_replay_resumes_topics_shells_tracks_and_dedicated_drives(campaign: ProposalCampaign) -> None:
    c = campaign
    manager = c.person("facilitator")
    with system_context(reason="tests.proposals.campaign.provision"):
        project = Project.objects.create(title="Provision target", owner=manager)
    template: dict[str, Any] = {
        "name": "Provisioned review",
        "opening_policy": "drafts_and_tracks",
        "last_call_at": c.now,
        "submission_deadline": c.now,
        "tracks": True,
        "topics": [{"key": "approach", "name": "Approach"}, {"key": "delivery", "name": "Delivery"}],
    }
    people = [c.person("responder"), c.person("peer")]
    before = relationship_snapshot()
    with actor_context(manager):
        first = Round.objects.provision(project, template, manager, people[:1])
        replay = Round.objects.provision(project, template, manager, people)
        assert replay.pk == first.pk
        for replacement in ({"opening_policy": "answers"}, {"topics": [{"key": "other", "name": "Other"}]}):
            with pytest.raises(ValidationError, match="different"):
                Round.objects.provision(project, {**template, **replacement}, manager, people)
    assert relationship_snapshot() == before
    assert Topic._base_manager.filter(round=first).count() == 2
    shells = list(Proposal._base_manager.filter(round=first).order_by("pk"))
    assert len(shells) == 2
    drives = list(Drive._base_manager.filter(project_bindings__project_id__in=[p.track_id for p in shells]))
    assert len(drives) == 2
    assert all(drive.owns_items and drive.owner_id is None for drive in drives)
    for shell in shells:
        track = Project._base_manager.get(pk=shell.track_id)
        assert track.owns_items and track.owner_id is None
        drive = Drive._base_manager.get(project_bindings__project=track)
        assert ProjectBinding._base_manager.filter(project=track, object_id=str(drive.pk)).count() == 1
        assert as_actor(drive, shell.responder).has_access("read")
        other = next(user for user in people if user.pk != shell.responder_id)
        assert not as_actor(drive, other).has_access("read")


@pytest.mark.parametrize("seat", ("facilitator", "moderator"))
def test_admitting_a_round_manager_rolls_back_the_shell(campaign: ProposalCampaign, seat: str) -> None:
    c = campaign
    round = c.round(team=c.team(moderator="moderator"))
    with pytest.raises(ValidationError, match="manager"):
        c.admit(round, seat, track=True)
    assert not Proposal._base_manager.filter(round=round).exists()


def test_facilitation_cannot_transfer_to_an_existing_or_retired_responder(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    manager = c.person("facilitator")
    with actor_context(manager):
        for retired in (False, True):
            if retired:
                as_actor(round, manager).remove_responder(c.person("responder"))
            with pytest.raises(ValidationError, match="responder"):
                as_actor(round, manager).transfer_facilitation(c.person("responder"))


@pytest.mark.parametrize("state", ("collecting", "canceled"))
def test_lift_waits_for_the_phase_after_its_boundary_and_backward_moves_never_erase_disclosure(
    campaign: ProposalCampaign, state: str,
) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder")
    with system_context(reason="tests.proposals.campaign.phases"):
        first = Milestone._base_manager.get(project_id=round.project_id)
        second = Milestone.objects.create(project=round.project, name="Build", sort_order=2048)
        third = Milestone.objects.create(project=round.project, name="Deliver", sort_order=3072)
        foreign = c.round().clarifications_shared_until
        row = Round.objects.get(pk=round.pk)
        row.opens_after = first
        row.save(update_fields=("opens_after",))
    owner, manager = c.person("project-owner"), c.person("facilitator")
    with actor_context(owner):
        for milestone in (third, foreign):
            with pytest.raises(ValidationError):
                as_actor(round.project, owner).set_current_milestone(milestone)
        as_actor(round.project, owner).set_current_milestone(first)
    with actor_context(manager):
        if state == "canceled":
            as_actor(round, manager).cancel()
        # The boundary phase itself keeps the round undisclosed: the lift is neither offered nor admitted.
        assert not as_actor(round, manager).can_open()
        with pytest.raises(ValidationError, match="opening phase"):
            as_actor(round, manager).open()
    assert Round._base_manager.get(pk=round.pk).opened_at is None
    with actor_context(owner):
        as_actor(round.project, owner).set_current_milestone(second)
    with actor_context(manager):
        assert as_actor(round, manager).can_open()
        opened = as_actor(round, manager).open()
    with actor_context(owner):
        as_actor(round.project, owner).set_current_milestone(first)
    assert not as_actor(round, manager).can_open()
    with actor_context(owner):
        as_actor(round.project, owner).set_current_milestone(third)
        as_actor(round.project, owner).set_current_milestone(first)
    lifted = Round._base_manager.get(pk=round.pk)
    assert (lifted.opened_at, lifted.status) == (opened.opened_at, "canceled" if state == "canceled" else "opened")
    assert Proposal._base_manager.get(pk=proposal.pk).disclosed_at == opened.opened_at


@pytest.mark.parametrize("state", ("collecting", "canceled"))
def test_manager_can_lift_any_undisclosed_round_and_reader_sees_receipt(
    campaign: ProposalCampaign, state: str,
) -> None:
    c = campaign
    round = c.round(policy="answers")
    proposal = c.admit(round, "responder")
    manager, reader = c.person("facilitator"), c.person("reader")
    with actor_context(manager):
        as_actor(proposal, manager).submit()
        if state == "canceled":
            round = as_actor(round, manager).cancel()
    grant(round, "reader", reader)
    assert as_actor(round, manager).can_open()
    assert not as_actor(round, reader).can_open()
    with actor_context(reader), pytest.raises(PermissionDenied):
        as_actor(round, reader).open()
    with actor_context(manager):
        lifted = as_actor(round, manager).open()
        revision = lifted.revision
        assert not as_actor(round, manager).can_open()
        replay = as_actor(round, manager).open(expected_revision=revision)
    assert lifted.status == ("opened" if state == "collecting" else "canceled")
    assert replay.revision == revision
    assert as_actor(round, reader).has_access("read")
    assert Round._base_manager.get(pk=round.pk).opened_at == c.now
    assert Proposal._base_manager.get(pk=proposal.pk).disclosed_at == c.now


def test_question_replay_is_asker_scoped_and_surrender_does_not_duplicate_content(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    c.admit(round, "peer")
    first = c.ask(round, audience="managers", idempotency_key="same-key")
    replay = c.ask(round, audience="managers", idempotency_key="same-key")
    other = c.ask(round, seat="peer", audience="managers", idempotency_key="same-key")
    assert first.pk == replay.pk != other.pk
    assert Task._base_manager.filter(clarification_round=round).count() == 2
    assert not as_actor(first, c.person("responder")).has_access("read")
    with actor_context(c.person("responder")), pytest.raises(CreationKeyConflict) as error:
        as_actor(round, c.person("responder")).ask(
            "Question", "Changed body", audience="managers", idempotency_key="same-key"
        )
    assert error.value.code == "CREATION_KEY_CONFLICT"
    assert first.created_by_id is None and first.updated_by_id is None and first.owner_id is None
    manager = c.person("facilitator")
    with actor_context(manager), pytest.raises(ValidationError, match="surrendered"):
        as_actor(round, manager).pass_clarification(first, c.person("peer"))
    with system_context(reason="tests.proposals.campaign.immutable_question"):
        row = Task.objects.get(pk=first.pk)
        row.visibility = "inherited"
        with pytest.raises(ValidationError, match="immutable"):
            row.save(update_fields=("visibility",))


def test_question_edit_checks_revision_and_first_pass_freezes_content(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    question = c.ask(round)
    asker, manager = c.person("responder"), c.person("facilitator")
    with actor_context(asker):
        edited = as_actor(round, asker).edit_clarification(
            question, "Edited", "Edited body", expected_revision=question.revision
        )
        assert edited.revision > question.revision
        with pytest.raises(StaleRevisionError):
            as_actor(round, asker).edit_clarification(
                question, "Lost edit", "Lost body", expected_revision=question.revision
            )
    with actor_context(manager):
        passed = as_actor(round, manager).pass_clarification(edited, c.person("recipient"), audience="asker")
        replay = as_actor(round, manager).pass_clarification(passed, c.person("recipient"), audience="asker")
        assert (passed.revision, passed.clarification_passed_at) == (replay.revision, replay.clarification_passed_at)
        with pytest.raises(ValidationError, match="audience"):
            as_actor(round, manager).pass_clarification(passed, c.person("recipient"), audience="default")
        with pytest.raises(ValidationError, match="content"):
            as_actor(round, manager).edit_clarification(passed, "Must not change", "Must not change")
    assert Task._base_manager.get(pk=question.pk).title == "Edited"


def test_question_default_is_snapshotted_at_the_boundary_and_ignores_later_phase_moves(
    campaign: ProposalCampaign,
) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    before = c.ask(round)
    with system_context(reason="tests.proposals.campaign.question_phase"):
        later = Milestone.objects.create(project=round.project, name="Later", sort_order=2048)
        Project._base_manager.filter(pk=round.project_id).update(current_milestone=round.clarifications_shared_until)
    at_boundary = c.ask(round)
    with system_context(reason="tests.proposals.campaign.question_later"):
        Project._base_manager.filter(pk=round.project_id).update(current_milestone=later)
    after = c.ask(round)
    assert (
        before.clarification_default_visibility,
        at_boundary.clarification_default_visibility,
        after.clarification_default_visibility,
    ) == ("inherited", "inherited", "restricted")
    manager = c.person("facilitator")
    with actor_context(manager):
        passed = as_actor(round, manager).pass_clarification(before, c.person("recipient"))
    assert passed.visibility == "inherited"
    with system_context(reason="tests.proposals.campaign.question_backward"):
        Project._base_manager.filter(pk=round.project_id).update(current_milestone=None)
    with actor_context(manager):
        passed = as_actor(round, manager).pass_clarification(after, c.person("recipient"))
    assert passed.visibility == "restricted"
    assert c.ask(round).clarification_default_visibility == "inherited"


def test_statement_alone_makes_a_draft_non_deletable(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder")
    assert proposal.delete_blocker() is None
    with actor_context(c.person("responder")):
        row = as_actor(proposal, c.person("responder"))
        row.statement = "A retained commitment"
        row.save(update_fields=("statement",))
        with pytest.raises(ValidationError, match="untouched"):
            row.delete()
    round = Round._base_manager.get(pk=round.pk)
    assert round.delete_blocker() is None
    preview = DeletePreview.from_instance(round)
    assert preview.has_blockers
    assert preview.refusals == ["Only an untouched draft proposal can be deleted."]


@pytest.mark.parametrize("path", ("instance", "queryset", "round_cascade", "task_cascade", "project_cascade"))
def test_proposal_delete_receiver_retains_touched_drafts_on_every_path(
    campaign: ProposalCampaign, path: str,
) -> None:
    """A retained draft cannot escape its model rule through a collector cascade."""

    c = campaign
    round = c.round(target_kind="task" if path == "task_cascade" else "project")
    if path == "project_cascade":
        with system_context(reason="tests.proposals.delete_receiver.target"):
            round.clarifications_shared_until = None
            round.save(update_fields=("clarifications_shared_until",))
    proposal = c.admit(round, "responder")
    stale = Proposal._base_manager.get(pk=proposal.pk)
    with actor_context(c.person("responder")):
        row = as_actor(proposal, c.person("responder"))
        row.statement = "A retained commitment"
        row.save(update_fields=("statement",))
    with system_context(reason="tests.proposals.delete_receiver"):
        with pytest.raises(ValidationError, match="untouched"):
            if path == "instance":
                stale.delete()
            elif path == "queryset":
                Proposal.objects.filter(pk=proposal.pk).delete()
            elif path == "round_cascade":
                Round.objects.filter(pk=round.pk).delete()
            elif path == "task_cascade":
                Task.objects.filter(pk=round.task_id).delete()
            else:
                Project.objects.filter(pk=round.project_id).delete()
    assert Proposal._base_manager.filter(pk=proposal.pk).exists()
    assert Round._base_manager.filter(pk=round.pk).exists()


@pytest.mark.parametrize("path", ("instance", "queryset"))
def test_proposal_delete_receiver_allows_untouched_drafts(campaign: ProposalCampaign, path: str) -> None:
    """Moving enforcement to receivers preserves deletion of untouched drafts."""

    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder")
    proposal_pk = proposal.pk
    with system_context(reason="tests.proposals.delete_receiver.allowed"):
        if path == "instance":
            proposal.delete()
        else:
            Proposal.objects.filter(pk=proposal.pk).delete()
    assert not Proposal._base_manager.filter(pk=proposal_pk).exists()
    assert Round._base_manager.filter(pk=round.pk).exists()


@pytest.mark.parametrize("path", ("instance", "queryset", "task_cascade"))
def test_round_delete_receiver_retains_terminal_rounds(campaign: ProposalCampaign, path: str) -> None:
    """Even an empty round retains its lifecycle rule through stale and cascade deletes."""

    c = campaign
    round = c.round(target_kind="task")
    stale = Round._base_manager.get(pk=round.pk)
    manager = c.person("facilitator")
    with actor_context(manager):
        as_actor(round, manager).cancel()
    with system_context(reason="tests.proposals.round_delete_receiver"):
        with pytest.raises(ValidationError, match="Only a collecting round can be deleted"):
            if path == "instance":
                stale.delete()
            elif path == "queryset":
                Round.objects.filter(pk=round.pk).delete()
            else:
                Task.objects.filter(pk=round.task_id).delete()
    assert Round._base_manager.filter(pk=round.pk, status="canceled").exists()
    assert Task._base_manager.filter(pk=round.task_id).exists()


def test_round_delete_receiver_allows_untouched_proposal_cascades(campaign: ProposalCampaign) -> None:
    """A collecting round still deletes its untouched proposal shells."""

    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder")
    round_pk, proposal_pk = round.pk, proposal.pk
    with system_context(reason="tests.proposals.round_delete_receiver.allowed"):
        round.delete()
    assert not Round._base_manager.filter(pk=round_pk).exists()
    assert not Proposal._base_manager.filter(pk=proposal_pk).exists()


@pytest.mark.parametrize("policy", ("answers", "drafts_and_tracks"))
def test_late_admission_obeys_the_opening_policy(campaign: ProposalCampaign, policy: str) -> None:
    """After opening only drafts-and-tracks admits, and a late shell is disclosed with its track at once."""

    c = campaign
    round = c.round(policy=policy)
    own = c.admit(round, "responder", track=True)
    manager = c.person("facilitator")
    with actor_context(manager):
        as_actor(own, manager).submit()
    c.open(round)
    before = Proposal._base_manager.count()
    if policy != "drafts_and_tracks":
        with pytest.raises(ValidationError, match="not accepting"):
            c.admit(round, "late", track=True)
        assert Proposal._base_manager.count() == before
        return
    shell = Proposal._base_manager.get(pk=c.admit(round, "late", track=True).pk)
    assert shell.disclosed_at is not None and shell.track_published_at is not None
    assert as_actor(own, c.person("late")).has_access("read")
    assert as_actor(shell, c.person("responder")).has_access("read")


@pytest.mark.parametrize("roster", ("hidden", "named", "status"))
def test_roster_lists_names_and_optional_status_by_roster_visibility(campaign: ProposalCampaign, roster: str) -> None:
    c = campaign
    round = c.round(roster_visibility=roster)
    c.admit(round, "responder", track=True)
    peer = c.admit(round, "peer", track=True)
    reader, manager = c.person("responder"), c.person("facilitator")
    everyone = {reader.pk, c.person("peer").pk}
    with actor_context(manager):
        entries = as_actor(round, manager).roster()
    assert {entry.user_id for entry in entries} == everyone
    assert all(entry.track_status == peer.track.status for entry in entries)
    with actor_context(reader):
        entries = as_actor(round, reader).roster()
    assert {entry.user_id for entry in entries} == (set() if roster == "hidden" else everyone)
    assert all(entry.track_status == (peer.track.status if roster == "status" else None) for entry in entries)


@pytest.mark.parametrize("model_name", ("round", "topic", "proposal"))
def test_proposal_save_leaves_unrelated_deferred_columns_unwritten(campaign: ProposalCampaign, model_name: str) -> None:
    """Invariant checks must preserve Django's loaded-fields-only UPDATE."""

    round = campaign.round()
    proposal = campaign.admit(round, "responder")
    with system_context(reason="tests.proposals.deferred_save"):
        topic = Topic.objects.create(round=round, key="scope", name="Original topic", sort_order=1024.0)
        rows = {"round": (round, "name"), "topic": (topic, "name"), "proposal": (proposal, "staffing")}
        row, field = rows[model_name]
        deferred = type(row)._base_manager.defer("created_at").get(pk=row.pk)
        assert "created_at" not in deferred.__dict__
        setattr(deferred, field, "Changed value")
        with CaptureQueriesContext(connection) as queries:
            deferred.save()
        updates = [query["sql"] for query in queries
                   if statement_startswith(query["sql"], f'UPDATE "{row._meta.db_table}"')]
        assert len(updates) == 1
        assert '"created_at" =' not in updates[0]
        stored = type(row)._base_manager.get(pk=row.pk)
        assert getattr(stored, field) == "Changed value"
        assert stored.created_at == row.created_at


def test_requester_identity_changes_only_through_the_round_instance(campaign: ProposalCampaign) -> None:
    """Collection updates and native upserts, even elevated, cannot bypass the locked round owner."""

    round = campaign.round()
    with system_context(reason="tests.proposals.requester.collection"):
        party = Person.objects.for_user(campaign.person("requester"))
        for manager in (Round.objects, Round._base_manager):
            with pytest.raises(ValidationError):
                manager.filter(pk=round.pk).update(requester_party=party)
    candidate = copy(round)
    candidate.requester_party = party
    for manager in (Round._base_manager, Round.objects.sudo(reason="tests.proposals.requester.conflict")):
        with pytest.raises(ValidationError, match="Change requester identity through the round instance"):
            manager.bulk_create([candidate], None, False, True, ["requester_party"], ["pk"])
        with pytest.raises(ValidationError, match="Change requester identity through the round instance"):
            manager.bulk_create(
                [candidate], update_conflicts=True, update_fields=["requester_party"], unique_fields=["pk"],
            )
    round.refresh_from_db()
    assert round.requester_party_id is None


def test_requester_party_delete_and_merge_use_the_native_collector_and_round_identity_owner(
    campaign: ProposalCampaign,
) -> None:
    manager = campaign.person("facilitator")
    with actor_context(manager), system_context(reason="tests.proposals.requester.lifecycle"):
        unreferenced = Person.objects.create(display_name="Unreferenced requester")
        unreferenced.delete()
        deleted = Person.objects.create(display_name="Disposable requester")
        cleared = campaign.round(requester_party=deleted)
        deleted.delete()
        cleared.refresh_from_db()
        assert cleared.requester_party_id is None
        source = Person.objects.create(display_name="Duplicate requester", owner=manager)
        survivor = Person.objects.create(display_name="Kept requester", owner=manager)
        merged = campaign.round(requester_party=source)
        survivor.merge(records=[source.public_id])
        merged.refresh_from_db()
        assert merged.requester_party_id == survivor.pk
