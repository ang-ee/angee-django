"""Lifecycle receipts, revision checks, provisioning and question replay contracts."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, actor_context, system_context
from rebac.models import active_relationship_model

from angee.base.mixins import CreationKeyConflict, StaleRevisionError
from angee.proposals.models import ClarificationWidenBlocked, PublishedQuestion
from tests.conftest import Drive
from tests.projects_models import Milestone, Project, ProjectBinding, Task
from tests.proposals_campaign import ProposalCampaign, as_actor, grant
from tests.proposals_models import Proposal, Round, Topic

pytest_plugins = ("tests.proposals_campaign",)


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
    before = active_relationship_model().objects.count()
    with actor_context(manager):
        first = Round.objects.provision(project, template, manager, people[:1])
        replay = Round.objects.provision(project, template, manager, people)
        assert replay.pk == first.pk
        for replacement in ({"opening_policy": "answers"}, {"topics": [{"key": "other", "name": "Other"}]}):
            with pytest.raises(ValidationError, match="different"):
                Round.objects.provision(project, {**template, **replacement}, manager, people)
    assert active_relationship_model().objects.count() == before
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


def test_lift_ignores_phase_and_backward_moves_never_erase_disclosure(campaign: ProposalCampaign) -> None:
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
        assert as_actor(round, manager).can_open()
        opened = as_actor(round, manager).open()
    with actor_context(owner):
        as_actor(round.project, owner).set_current_milestone(second)
        as_actor(round.project, owner).set_current_milestone(first)
    assert not as_actor(round, manager).can_open()
    with actor_context(owner):
        as_actor(round.project, owner).set_current_milestone(third)
        as_actor(round.project, owner).set_current_milestone(first)
    assert Round._base_manager.get(pk=round.pk).opened_at == opened.opened_at
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
    first = c.ask(round, audience="managers", client_creation_key="same-key")
    replay = c.ask(round, audience="managers", client_creation_key="same-key")
    other = c.ask(round, seat="peer", audience="managers", client_creation_key="same-key")
    assert first.pk == replay.pk != other.pk
    assert Task._base_manager.filter(clarification_round=round).count() == 2
    assert not as_actor(first, c.person("responder")).has_access("read")
    with actor_context(c.person("responder")), pytest.raises(CreationKeyConflict) as error:
        as_actor(round, c.person("responder")).ask(
            "Question", "Changed body", audience="managers", client_creation_key="same-key"
        )
    assert error.value.code == "CREATION_KEY_CONFLICT"
    assert first.created_by_id is None and first.updated_by_id is None and first.owner_id is None


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
    assert proposal.deletion_error() is None
    with actor_context(c.person("responder")):
        row = as_actor(proposal, c.person("responder"))
        row.statement = "A retained commitment"
        row.save(update_fields=("statement",))
        with pytest.raises(ValidationError, match="untouched"):
            row.delete()
    assert Round._base_manager.get(pk=round.pk).deletion_error() is not None


def test_hidden_asker_message_prevents_widening_and_default_pass_at_the_model_owner(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    task = c.ask(round)
    manager, asker = c.person("facilitator"), c.person("responder")
    with actor_context(manager):
        as_actor(task, manager).set_visibility("restricted")
    with actor_context(asker):
        as_actor(task, asker).message_post("Keep this identity private")
    before = Task._base_manager.filter(pk=task.pk).values().get()
    with actor_context(manager):
        with pytest.raises(ClarificationWidenBlocked) as error:
            as_actor(task, manager).set_visibility("inherited")
        assert error.value.code == "HIDDEN_ASKER_IN_THREAD"
        with pytest.raises(ClarificationWidenBlocked):
            as_actor(round, manager).pass_clarification(task, c.person("recipient"))
    assert Task._base_manager.filter(pk=task.pk).values().get() == before


def test_published_question_refuses_narrowing_and_stale_hidden_asker_posts(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    task = c.ask(round)
    manager, asker = c.person("facilitator"), c.person("responder")
    stale = as_actor(task, asker)
    with actor_context(manager):
        as_actor(round, manager).pass_clarification(task, c.person("recipient"))
        with pytest.raises(PublishedQuestion) as error:
            as_actor(task, manager).set_visibility("restricted")
        assert error.value.code == "PUBLISHED_QUESTION"
    with actor_context(asker):
        for method in (stale.message_post, stale.message_log):
            with pytest.raises(ClarificationWidenBlocked):
                method("Must never be published")
    assert Task._base_manager.get(pk=task.pk).visibility == "inherited"
