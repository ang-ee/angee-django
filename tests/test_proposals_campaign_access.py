"""Seat, disclosure, retirement and live-share contracts in both relationship stores."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from rebac import ObjectRef, PermissionDenied, SubjectRef, actor_context, system_context, to_subject_ref
from rebac.models import active_relationship_model

from angee.base.errors import RecordAccessSubjectRefused
from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload
from tests.conftest import Drive, File, FileAttachment
from tests.messaging_models import Person
from tests.projects_models import Milestone, Project, Task
from tests.proposals_campaign import ProposalCampaign, as_actor, grant
from tests.proposals_models import Answer, Proposal, Round, Topic

pytest_plugins = ("tests.proposals_campaign",)


def assert_read(row: Any, actor: Any, expected: bool) -> None:
    """Require the single-row decision and SQL read scope to agree."""
    with actor_context(actor):
        assert as_actor(row, actor).has_access("read") is expected, (row._meta.label, actor, expected)
        assert type(row).objects.as_user(actor).filter(pk=row.pk).exists() is expected, (
            row._meta.label,
            actor,
            expected,
        )


def test_project_writer_reads_widened_question_without_comment_authority(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    question = c.ask(round)
    project_writer = c.person("project-owner")
    assert_read(question, project_writer, True)
    assert not as_actor(question, project_writer).has_access("comment")
    with actor_context(project_writer), pytest.raises(PermissionDenied):
        as_actor(question, project_writer).message_post("Not addressed to me")


def test_removing_responder_ends_their_round_follow(campaign: ProposalCampaign) -> None:
    """Retiring the seat removes its reader's follow on the affected record."""

    c = campaign
    round = c.round()
    responder = c.person("responder")
    c.admit(round, "responder")
    with system_context(reason="tests.proposals.responder_follow"):
        round.message_subscribe(user=responder)
    assert round.message_is_follower(user=responder)
    manager = c.person("facilitator")
    with actor_context(manager):
        as_actor(round, manager).remove_responder(responder)
    assert not round.message_is_follower(user=responder)


def test_admitting_a_following_responder_grants_read_and_follow_together(campaign: ProposalCampaign) -> None:
    """The role owner's opt-in admission writes both facts atomically."""

    c = campaign
    round = c.round()
    responder = c.person("new-responder")
    manager = c.person("facilitator")
    with actor_context(manager):
        as_actor(round, manager).admit(responder, follow=True)
    assert_read(round, responder, True)
    assert round.message_is_follower(user=responder)


@pytest.mark.parametrize("policy", ("facilitator_only", "answers", "answers_and_tracks", "drafts_and_tracks"))
@pytest.mark.parametrize("target_kind", ("project", "task"))
def test_each_seat_reads_only_its_round_proposals_tracks_questions_and_answers(
    campaign: ProposalCampaign,
    policy: str,
    target_kind: str,
) -> None:
    c = campaign
    team = c.team(moderator="moderator", member="member", viewer="viewer")
    requester = c.person("requester")
    with system_context(reason="tests.proposals.campaign.requester"):
        requester_party = Person.objects.for_user(requester)
    round = c.round(policy=policy, target_kind=target_kind, team=team, requester_party=requester_party)
    own = c.admit(round, "responder", track=True)
    peer = c.admit(round, "peer")
    c.admit(round, "retired")
    manager = c.person("facilitator")
    with actor_context(manager):
        as_actor(round, manager).remove_responder(c.person("retired"))
        as_actor(own, manager).submit()
        as_actor(peer, manager).submit()
    answer = c.answer(own)
    question = c.ask(round)
    grant(round, "evaluator", c.person("evaluator"))
    grant(ObjectRef("proposals/role", "proposal_viewer"), "member", c.person("global-viewer"))
    rows = (round, own, own.track, question, answer)
    for opened in (False, True):
        if opened:
            c.open(round)
        disclosed = opened and policy != "facilitator_only"
        tracks = opened and policy in {"answers_and_tracks", "drafts_and_tracks"}
        expected = {
            "facilitator": (True, True, True, True, True),
            "moderator": (True, True, True, True, True),
            "member": (True, False, False, False, False),
            "viewer": (False, False, False, False, False),
            "responder": (True, True, True, True, True),
            "peer": (True, disclosed, tracks, True, disclosed),
            "retired": (False, False, False, False, False),
            "requester": (True, True, False, False, True),
            "evaluator": (True, True, False, False, True),
            "global-viewer": (True, True, False, False, True),
            "project-owner": (False, False, False, True, False),
            "outsider": (False, False, False, False, False),
        }
        for seat, permissions in expected.items():
            actor = c.person(seat)
            assert not actor.is_superuser
            for row, allowed in zip(rows, permissions, strict=True):
                assert_read(row, actor, allowed)
        for row in rows:
            assert_read(row, AnonymousUser(), False)


@pytest.mark.parametrize("policy", ("facilitator_only", "answers", "answers_and_tracks", "drafts_and_tracks"))
def test_late_admission_obeys_each_opening_policy(campaign: ProposalCampaign, policy: str) -> None:
    c = campaign
    round = c.round(policy=policy)
    own = c.admit(round, "responder", track=True)
    answer = c.answer(own)
    question = c.ask(round)
    manager = c.person("facilitator")
    with actor_context(manager):
        as_actor(own, manager).submit()
    c.open(round)
    late = c.person("late")
    before = Proposal._base_manager.count()
    if policy != "drafts_and_tracks":
        with pytest.raises(ValidationError, match="not accepting"):
            c.admit(round, "late", track=True)
        assert Proposal._base_manager.count() == before
        for row in (round, own, own.track, question, answer):
            assert_read(row, late, False)
    else:
        shell = c.admit(round, "late", track=True)
        assert shell.disclosed_at is not None
        assert Proposal._base_manager.get(pk=shell.pk).track_published_at is not None
        for row in (round, own, own.track, question, answer):
            assert_read(row, late, True)
        assert_read(shell, c.person("responder"), True)


@pytest.mark.parametrize("roster", ("hidden", "named", "status"))
def test_roster_discloses_only_names_and_optional_status_without_peer_rows(
    campaign: ProposalCampaign,
    roster: str,
) -> None:
    c = campaign
    round = c.round(roster_visibility=roster)
    own = c.admit(round, "responder", track=True)
    peer = c.admit(round, "peer", track=True)
    reader = c.person("responder")
    manager = c.person("facilitator")
    with actor_context(manager):
        entries = as_actor(round, manager).roster()
        assert {entry.user_id for entry in entries} == {reader.pk, c.person("peer").pk}
        assert all(entry.track_status == peer.track.status for entry in entries)
    with actor_context(reader):
        entries = as_actor(round, reader).roster()
        assert {entry.user_id for entry in entries} == (
            set() if roster == "hidden" else {reader.pk, c.person("peer").pk}
        )
        assert (
            all(entry.track_status is None for entry in entries)
            if roster != "status"
            else all(entry.track_status == peer.track.status for entry in entries)
        )
        assert set(Proposal.objects.as_user(reader).values_list("pk", flat=True)) == {own.pk}
        assert Proposal.objects.as_user(reader).filter(round=round).count() == 1
        assert not as_actor(peer, reader).has_access("read")
        assert not as_actor(round, reader).has_access("read__roster_visibility")
        assert not as_actor(round, reader).has_access("read__clarification_askers")
    c.open(round)
    assert as_actor(peer, reader).has_access("read__responder") is (roster != "hidden")
    for field in (
        "cost",
        "currency",
        "staffing",
        "confidence",
        "timeframe_start",
        "timeframe_end",
        "valid_until",
        "source_message",
        "statement",
    ):
        assert not as_actor(peer, reader).has_access(f"read__{field}")
        assert as_actor(peer, c.person("facilitator")).has_access(f"read__{field}")


@pytest.mark.parametrize("visibility", ("round", "responder", "sealed"))
def test_answer_audience_at_insert_and_after_opening_for_six_seats(
    campaign: ProposalCampaign,
    visibility: str,
) -> None:
    c = campaign
    with system_context(reason="tests.proposals.campaign.requester"):
        party = Person.objects.for_user(c.person("requester"))
    round = c.round(requester_party=party)
    proposal = c.admit(round, "responder")
    c.admit(round, "peer")
    grant(round, "evaluator", c.person("evaluator"))
    grant(ObjectRef("proposals/role", "proposal_viewer"), "member", c.person("global-viewer"))
    answer = c.answer(proposal, visibility=visibility)
    for opened in (False, True):
        if opened:
            c.open(round)
        for seat, allowed in (
            ("facilitator", True),
            ("responder", visibility != "sealed"),
            ("peer", opened and visibility == "round"),
            ("requester", visibility != "sealed"),
            ("evaluator", visibility != "sealed"),
            ("global-viewer", visibility != "sealed"),
        ):
            assert_read(answer, c.person(seat), allowed)


@pytest.mark.parametrize("seat", ("facilitator", "responder", "peer", "requester", "evaluator", "outsider"))
@pytest.mark.parametrize("visibility", ("round", "responder", "sealed"))
def test_only_managers_and_own_responders_can_change_answer_audience(
    campaign: ProposalCampaign,
    seat: str,
    visibility: str,
) -> None:
    c = campaign
    with system_context(reason="tests.proposals.campaign.requester"):
        party = Person.objects.for_user(c.person("requester"))
    round = c.round(requester_party=party)
    proposal = c.admit(round, "responder")
    c.admit(round, "peer")
    grant(round, "evaluator", c.person("evaluator"))
    answer = c.answer(proposal)
    actor = c.person(seat)
    with actor_context(actor):
        if seat in {"facilitator", "responder"}:
            as_actor(answer, actor).set_visibility(visibility)
            assert Answer._base_manager.get(pk=answer.pk).visibility == visibility
        else:
            with pytest.raises(PermissionDenied):
                as_actor(answer, actor).set_visibility(visibility)
            assert Answer._base_manager.get(pk=answer.pk).visibility == "round"


def test_responder_can_narrow_but_cannot_restore_an_answer(campaign: ProposalCampaign) -> None:
    c = campaign
    answer = c.answer(c.admit(c.round(), "responder"))
    actor = c.person("responder")
    with actor_context(actor):
        as_actor(answer, actor).set_visibility("responder")
        with pytest.raises(PermissionDenied):
            as_actor(answer, actor).set_visibility("round")
        as_actor(answer, actor).set_visibility("sealed")
        with pytest.raises(PermissionDenied):
            as_actor(answer, actor).set_visibility("responder")
    assert_read(answer, actor, False)


@pytest.mark.parametrize("seat", ("facilitator", "responder", "peer", "requester", "evaluator", "outsider"))
@pytest.mark.parametrize("visibility", ("round", "responder", "sealed"))
def test_answer_insert_authorizes_the_seat_before_creating_an_already_narrowed_response(
    campaign: ProposalCampaign,
    seat: str,
    visibility: str,
) -> None:
    c = campaign
    with system_context(reason="tests.proposals.campaign.insert_requester"):
        requester = Person.objects.for_user(c.person("requester"))
    round = c.round(requester_party=requester)
    proposal = c.admit(round, "responder")
    c.admit(round, "peer")
    grant(round, "evaluator", c.person("evaluator"))
    with system_context(reason="tests.proposals.campaign.insert_topic"):
        topic = Topic.objects.create(round=round, key="approach", name="Approach")
    actor = c.person(seat)
    with actor_context(actor):
        if seat not in {"facilitator", "responder"}:
            with pytest.raises(PermissionDenied):
                Answer.objects.check_create({"proposal": (proposal,), "topic": (topic,)})
            assert not Answer._base_manager.filter(proposal=proposal).exists()
            return
        Answer.objects.check_create({"proposal": (proposal,), "topic": (topic,)})
        # The existing manual factory convention preflights under the caller,
        # then performs the authorized insert with the native system binding.
        answer = Answer(proposal=proposal, topic=topic, body="Answer", visibility=visibility)
        answer.sudo(reason="tests.proposals.campaign.authorized_answer").save()
    assert Answer._base_manager.get(pk=answer.pk).visibility == visibility
    assert_read(answer, c.person("peer"), False)
    c.open(round)
    assert_read(answer, c.person("peer"), visibility == "round")


def test_party_without_an_account_grants_no_requester_reach(campaign: ProposalCampaign) -> None:
    c = campaign
    with system_context(reason="tests.proposals.campaign.no_account"):
        party = Person.objects.create(display_name="External requester")
    round = c.round(requester_party=party)
    proposal = c.admit(round, "responder")
    answer = c.answer(proposal)
    for row in (round, proposal, answer, round.project):
        assert_read(row, c.person("outsider"), False)
    assert party.user_id is None


def test_named_task_and_document_shares_do_not_expand_to_the_track_or_late_responders(
    campaign: ProposalCampaign,
) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder", track=True)
    c.admit(round, "peer")
    with system_context(reason="tests.proposals.campaign.named_items"):
        task = Task.objects.create(project=proposal.track, title="One private item", visibility="restricted")
        drive = Drive.objects.get(project_bindings__project=proposal.track)
        file = File.objects.create(
            drive=drive, filename="Details.txt", storage_path="details.txt", content_hash="d" * 64, visibility="record"
        )
        FileAttachment.objects.attach(file, task)
    manager, responder, peer = c.person("facilitator"), c.person("responder"), c.person("peer")
    for row, relation in ((task, "reader"), (file, "viewer")):
        with actor_context(manager):
            as_actor(row, manager).grant_record_access(relation, peer)
        with actor_context(responder), pytest.raises(PermissionDenied):
            as_actor(row, responder).grant_record_access(relation, peer)
    c.admit(round, "late")
    assert_read(proposal.track, peer, False)
    for row in (task, file):
        assert_read(row, peer, True)
        assert_read(row, c.person("late"), False)
    with actor_context(manager):
        as_actor(task, manager).revoke_record_access("reader", peer)
    assert_read(task, peer, False)
    assert_read(file, peer, True)
    with actor_context(manager):
        as_actor(file, manager).revoke_record_access("viewer", peer)
    assert_read(file, peer, False)


def test_live_responder_share_includes_late_admission_and_excludes_retirement(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder", track=True)
    c.admit(round, "peer")
    answer = c.answer(proposal, visibility="responder")
    with system_context(reason="tests.proposals.campaign.shared_task"):
        task = Task.objects.create(project=proposal.track, title="Private detail", visibility="restricted")
        drive = Drive.objects.get(project_bindings__project_id=proposal.track_id)
        document = File.objects.create(
            drive=drive,
            filename="Private.txt",
            content_hash="c" * 64,
            storage_path="private.txt",
            visibility="record",
        )
        FileAttachment.objects.attach(document, task)
    manager = c.person("facilitator")
    before = active_relationship_model().objects.count()
    with actor_context(manager):
        for item in (task, answer):
            as_actor(item, manager).set_responder_share(True, expected_revision=item.revision)
    assert active_relationship_model().objects.count() == before
    c.admit(round, "late")
    for seat in ("peer", "late"):
        assert_read(document, c.person(seat), True)
    for item in (task, answer):
        for seat in ("peer", "late"):
            assert_read(item, c.person(seat), True)
        with actor_context(c.person("responder")), pytest.raises(PermissionDenied):
            as_actor(item, c.person("responder")).set_responder_share(True)
    with actor_context(manager):
        as_actor(round, manager).remove_responder(c.person("peer"))
    assert_read(document, c.person("peer"), False)
    for item in (task, answer):
        assert_read(item, c.person("peer"), False)
        assert_read(item, c.person("late"), True)
        with actor_context(manager):
            as_actor(item, manager).set_responder_share(False)
        assert_read(item, c.person("late"), False)
    assert_read(document, c.person("late"), False)


def test_named_answer_share_is_independent_of_opening_and_late_admission(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder")
    c.admit(round, "peer")
    answer = c.answer(proposal, visibility="sealed")
    manager, peer = c.person("facilitator"), c.person("peer")
    with actor_context(manager):
        as_actor(answer, manager).grant_record_access("reader", peer)
    c.admit(round, "late")
    c.open(round)
    assert_read(answer, peer, True)
    assert_read(answer, c.person("late"), False)
    with actor_context(manager):
        as_actor(answer, manager).revoke_record_access("reader", peer)
    assert_read(answer, peer, False)


def test_retirement_cleans_peers_tracks_and_withholds_group_shared_round_content(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    own = c.admit(round, "responder", track=True)
    peer = c.admit(round, "peer", track=True)
    retiring, manager = c.person("responder"), c.person("facilitator")
    own_question = c.ask(round)
    shared_question = c.ask(round, seat="peer")
    with actor_context(manager):
        assigned = as_actor(round, manager).ask("Assigned question", "Reply", recipient=retiring)
    answer = c.answer(peer, visibility="sealed")
    with system_context(reason="tests.proposals.campaign.retirement"):
        group = apps.get_model("iam", "Group").objects.create(name=f"{c.prefix}-reviewers")
        peer_task = Task.objects.create(project=peer.track, title="Peer work")
        # Legacy ownership and assignment are deliberately seeded below owning-container defaults.
        Task._base_manager.filter(pk=peer_task.pk).update(owner=retiring, assignee=retiring)
        drive = Drive._base_manager.get(project_bindings__project_id=peer.track_id)
        file = File.objects.create(drive=drive, filename="Review.txt", storage_path="review.txt", content_hash="a" * 64)
        ordinary = Task.objects.create(project=round.project, title="Unrelated work")
    grant(group, "member", retiring)
    grant(group, "member", c.person("other-member"))
    subject = SubjectRef.of("auth/group", str(group.pk), "member")
    for row, relation in ((round, "reader"), (peer, "reader"), (answer, "reader"), (own_question, "reader")):
        grant(row, relation, subject)
        grant(row, relation, retiring)
    track_rows = (
        (own.track, "reader"),
        (peer.track, "editor"),
        (peer_task, "reader"),
        (drive, "viewer"),
        (file, "viewer"),
    )
    for row, relation in track_rows:
        grant(row, relation, subject)
        grant(row, relation, retiring)
    grant(round.project, "reader", retiring)
    assert_read(answer, retiring, True)
    with actor_context(manager):
        report = as_actor(round, manager).remove_responder(retiring)
    removed = {(row["resource"], row["relation"], row["subject"]) for row in report["removed"]}
    for row, relation in track_rows:
        assert (f"{row._meta.rebac_resource_type}:{row.pk}", relation, str(subject)) in removed
        assert_read(row, retiring, False)
        assert_read(row, c.person("other-member"), False)
    for row in (round, own, peer, answer, own_question, shared_question, assigned):
        assert_read(row, retiring, False)
    assert any(row["resource"] == f"proposals/answer:{answer.pk}" for row in report["reported"])
    assert_read(answer, c.person("other-member"), True)
    assert_read(round.project, retiring, True)
    assert_read(ordinary, retiring, True)
    stored = Task._base_manager.get(pk=peer_task.pk)
    assert stored.owner_id is None and stored.assignee_id is None
    assert Task._base_manager.get(pk=assigned.pk).assignee_id is None
    assert Task._base_manager.get(pk=own_question.pk).clarification_asker_id == retiring.pk
    with actor_context(manager):
        assert as_actor(round, manager).remove_responder(retiring) == {"removed": [], "reported": []}
        with pytest.raises(ValidationError, match="retired"):
            as_actor(round, manager).admit(retiring)


def test_retirement_reports_a_wildcard_without_deleting_unrelated_audiences(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder", track=True)
    drive = Drive._base_manager.get(project_bindings__project_id=proposal.track_id)
    wildcard = SubjectRef.of("auth/user", "*")
    grant(drive, "viewer", wildcard)
    manager = c.person("facilitator")
    with actor_context(manager):
        report = as_actor(round, manager).remove_responder(c.person("responder"))
    assert {"resource": f"storage/drive:{drive.pk}", "relation": "viewer", "subject": str(wildcard)} in report[
        "reported"
    ]
    assert (
        active_relationship_model()
        .objects.filter(
            resource_type="storage/drive",
            resource_id=str(drive.pk),
            relation="viewer",
            subject_id="*",
        )
        .exists()
    )
    assert_read(drive, c.person("outsider"), True)


@pytest.mark.parametrize("subject_kind", ("user", "group", "wildcard"))
def test_requester_ceiling_refuses_every_build_share_without_writing_a_tuple(
    campaign: ProposalCampaign,
    subject_kind: str,
) -> None:
    c = campaign
    requester = c.person("requester")
    with system_context(reason="tests.proposals.campaign.ceiling"):
        party = Person.objects.for_user(requester)
        group = apps.get_model("iam", "Group").objects.create(name=f"{c.prefix}-requesters")
    grant(group, "member", requester)
    subject = {
        "user": to_subject_ref(requester),
        "group": SubjectRef.of("auth/group", str(group.pk), "member"),
        "wildcard": SubjectRef.of("auth/user", "*"),
    }[subject_kind]
    round = c.round(requester_party=party)
    proposal = c.admit(round, "responder", track=True)
    answer = c.answer(proposal)
    with system_context(reason="tests.proposals.campaign.ceiling_content"):
        task = Task.objects.create(project=proposal.track, title="Track task")
        drive = Drive.objects.get(project_bindings__project_id=proposal.track_id)
        file = File.objects.create(drive=drive, filename="Build.txt", storage_path="build.txt", content_hash="b" * 64)
    before = active_relationship_model().objects.count()
    manager = c.person("facilitator")
    with actor_context(manager):
        for row, relation in (
            (proposal, "reader"),
            (answer, "reader"),
            (proposal.track, "reader"),
            (task, "reader"),
            (drive, "viewer"),
            (drive, "editor"),
            (file, "viewer"),
        ):
            with pytest.raises(RecordAccessSubjectRefused):
                as_actor(row, manager).grant_record_access(relation, subject)
    assert active_relationship_model().objects.count() == before


def test_requester_cannot_enter_through_admission_or_direct_shell_insert(campaign: ProposalCampaign) -> None:
    c = campaign
    requester = c.person("requester")
    with system_context(reason="tests.proposals.campaign.requester"):
        party = Person.objects.for_user(requester)
    round = c.round(requester_party=party)
    manager = c.person("facilitator")
    with actor_context(manager):
        with pytest.raises(RecordAccessSubjectRefused):
            as_actor(round, manager).admit(requester)
        with pytest.raises(RecordAccessSubjectRefused):
            Proposal(round=round, responder=requester).sudo(reason="tests.proposals.campaign.shell").save()
    assert not Proposal._base_manager.filter(round=round, responder=requester).exists()
    other = c.round()
    assert c.admit(other, "requester").responder_id == requester.pk
    c.admit(round, "responder")
    question = c.ask(round)
    with actor_context(manager):
        as_actor(question, manager).grant_record_access("reader", requester)
    assert_read(question, requester, True)


def test_requester_cannot_be_assigned_a_track_task_or_receive_track_ownership(campaign: ProposalCampaign) -> None:
    c = campaign
    requester = c.person("requester")
    with system_context(reason="tests.proposals.campaign.requester_holder"):
        party = Person.objects.for_user(requester)
    round = c.round(requester_party=party)
    proposal = c.admit(round, "responder", track=True)
    with system_context(reason="tests.proposals.campaign.requester_task"):
        task = Task.objects.create(project=proposal.track, title="Build detail")
    manager = c.person("facilitator")
    with system_context(reason="tests.proposals.campaign.legacy_track_owner"):
        # An owned legacy row reaches the holder ceiling through the authorized
        # owner-transfer verb; ordinary round management does not grant transfer.
        Project._base_manager.filter(pk=proposal.track_id).update(owner=manager)
    with actor_context(manager):
        row = as_actor(task, manager)
        row.assignee = requester
        with pytest.raises(RecordAccessSubjectRefused):
            row.save(update_fields=("assignee",))
        with pytest.raises(RecordAccessSubjectRefused):
            as_actor(proposal.track, manager).transfer_ownership(requester)
    assert Task._base_manager.get(pk=task.pk).assignee_id is None
    assert Project._base_manager.get(pk=proposal.track_id).owner_id == manager.pk


def test_responder_may_clear_but_never_set_a_live_share_on_own_inherited_items(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder", track=True)
    c.admit(round, "peer")
    answer = c.answer(proposal, visibility="responder")
    with system_context(reason="tests.proposals.campaign.share_clear"):
        task = Task.objects.create(project=proposal.track, title="Shareable work")
    manager, responder = c.person("facilitator"), c.person("responder")
    for item in (task, answer):
        with actor_context(manager):
            as_actor(item, manager).set_responder_share(True)
        assert_read(item, c.person("peer"), True)
        with actor_context(responder):
            as_actor(item, responder).set_responder_share(False)
            with pytest.raises(PermissionDenied):
                as_actor(item, responder).set_responder_share(True)
        assert_read(item, c.person("peer"), False)


def test_shells_and_requester_party_are_the_only_admission_and_requester_facts(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    responder = c.person("responder")
    requester = c.person("requester")
    assert not as_actor(round, responder).has_access("respond")
    c.admit(round, "responder")
    assert as_actor(round, responder).has_access("respond")
    manager = c.person("facilitator")
    with actor_context(manager):
        for relation in ("responder", "requester"):
            with pytest.raises(ValueError, match="grantable"):
                as_actor(round, manager).grant_record_access(relation, requester)
    assert (
        not active_relationship_model()
        .objects.filter(
            resource_type="proposals/round",
            resource_id=str(round.pk),
            relation__in=("responder", "requester"),
        )
        .exists()
    )
    assert_read(round, requester, False)
    with system_context(reason="tests.proposals.campaign.party"):
        row = Round.objects.get(pk=round.pk)
        row.requester_party = Person.objects.for_user(requester)
        row.save(update_fields=("requester_party",))
    assert_read(round, requester, True)
    assert_read(round.project, requester, False)
    for permission in ("write", "share", "delete"):
        assert not as_actor(round.project, responder).has_access(permission)
    assert Milestone.objects.as_user(responder).filter(project=round.project).exists()


def test_round_team_and_project_team_do_not_copy_each_others_seats(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round(team=c.team(round_member="member", round_moderator="moderator"))
    project_team = c.team(project_member="member", project_moderator="moderator")
    with system_context(reason="tests.proposals.campaign.project_team"):
        Project._base_manager.filter(pk=round.project_id).update(team=project_team)
    for seat in ("project_member", "project_moderator"):
        assert_read(round.project, c.person(seat), True)
        assert_read(round, c.person(seat), False)
    for seat in ("round_member", "round_moderator"):
        assert_read(round, c.person(seat), True)
        assert_read(round.project, c.person(seat), False)
    inactive = c.person("facilitator")
    with system_context(reason="tests.proposals.campaign.deactivate"):
        type(inactive)._base_manager.filter(pk=inactive.pk).update(is_active=False)
    inactive.refresh_from_db()
    assert not as_actor(round, inactive).has_access("manage")


def test_view_as_reaches_only_current_responders_of_managed_rounds(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    c.admit(round, "peer")
    manager = c.person("facilitator")
    for seat in ("responder", "peer"):
        assert as_actor(c.person(seat), manager).has_access("view_as")
    assert not as_actor(c.person("outsider"), manager).has_access("view_as")
    assert not as_actor(manager, manager).has_access("view_as")
    with actor_context(manager):
        as_actor(round, manager).remove_responder(c.person("peer"))
    assert not as_actor(c.person("peer"), manager).has_access("view_as")


def test_subscription_gate_drops_sealed_answer_events_and_redacts_disclosed_offer_fields(
    campaign: ProposalCampaign,
) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder")
    c.admit(round, "peer")
    answer = c.answer(proposal, visibility="sealed")
    peer = c.person("peer")
    answer_gate = ChangeReadGate(Answer, to_subject_ref(peer))
    proposal_gate = ChangeReadGate(Proposal, to_subject_ref(peer))
    answer_change = ChangePayload(
        model=Answer._meta.label,
        id=str(answer.sqid),
        resource_id=str(answer.pk),
        action="update",
        changed_fields=("body",),
        changed_values={"body": "Secret reply"},
    )
    proposal_change = ChangePayload(
        model=Proposal._meta.label,
        id=str(proposal.sqid),
        resource_id=str(proposal.pk),
        action="update",
        changed_fields=("state", "statement", "responder"),
        changed_values={
            "state": "draft",
            "statement": "Secret commitment",
            "responder": str(c.person("responder").sqid),
        },
    )
    with actor_context(peer):
        assert answer_gate.filter(answer_change) is None
        assert proposal_gate.filter(proposal_change) is None
    c.open(round)
    with actor_context(peer):
        assert answer_gate.filter(answer_change) is None
        event = proposal_gate.filter(proposal_change)
        assert event is not None
        assert event.changed_values == {"state": "draft"}
        assert set(event.changed_fields or ()) == {"state"}
