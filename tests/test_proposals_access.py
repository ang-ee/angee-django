"""The proposals decision table, each enforcement point, and the verbs that write proposal grants."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from rebac import ObjectRef, PermissionDenied, SubjectRef, actor_context, system_context, to_subject_ref
from rebac.models import active_relationship_model
from rebac.testing import assert_decisions

from angee.base.exceptions import RecordAccessSubjectRefused
from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload
from angee.messaging.testing.models import Person, ThreadAttachment
from angee.projects.testing.models import Project, Task
from angee.spaces.testing.models import Membership
from tests.conftest import Drive, File, FileAttachment, create_platform_admin
from tests.decision_table import seat_cells
from tests.proposals_campaign import ProposalCampaign, as_actor, grant
from tests.proposals_campaign import campaign as campaign
from tests.proposals_campaign import campaign_schema as campaign_schema
from tests.proposals_models import Answer, Proposal, Review, Round, Topic


def test_the_policy_decides_every_proposals_permission(campaign: ProposalCampaign) -> None:
    """One world, every declared permission with an allow and a deny, and the read scopes it compiles."""

    c = campaign
    u = c.person
    admin = create_platform_admin(f"{c.prefix}-admin")
    viewer_role = ObjectRef("proposals/role", "proposal_viewer")
    with system_context(reason="tests.proposals.policy.parties"):
        requester_party = Person.objects.for_user(u("requester"))
        service = type(u("outsider")).objects.create_user(username=f"{c.prefix}-service", kind="service")

    # Round one: opened, answers and tracks, a named roster, a round team and a requester.
    team = c.team(**{"moderator": "moderator", "member": "member", "team-viewer": "viewer",
                     "inactive-moderator": "moderator"})
    round1 = c.round(policy="answers_and_tracks", team=team, requester_party=requester_party, roster_visibility="named")
    project1 = round1.project
    prop_r, prop_p = c.admit(round1, "responder", track=True), c.admit(round1, "peer")
    prop_d, prop_x = c.admit(round1, "drafter"), c.admit(round1, "retired")
    prop_former = c.admit(round1, "former")
    with system_context(reason="tests.proposals.policy.round1"):
        Project._base_manager.filter(pk=project1.pk).update(team=c.team(**{"project-member": "member"}))
        topic = Topic.objects.create(round=round1, key="approach", name="Approach")
        a_round, a_resp, a_sealed, a_live = (
            Answer.objects.create(
                proposal=prop_r, topic=Topic.objects.create(round=round1, key=key, name=key), visibility=visibility,
            )
            for key, visibility in (("a", "round"), ("b", "responder"), ("c", "sealed"), ("d", "responder"))
        )
        a_draft = Answer.objects.create(proposal=prop_d, topic=topic, visibility="round")
        review = Review.objects.create(proposal=prop_r, reviewer=u("evaluator"))
    for row, relation, seat in (
        (round1, "evaluator", "evaluator"), (round1, "reader", "reader"), (round1, "reader", "retired"),
        (round1, "proposal_viewer", "round-viewer"), (project1, "proposal_viewer", "project-viewer"),
        (project1, "proposal_viewer", service), (prop_r, "reader", "proposal-reader"), (prop_r, "reader", "retired"),
        (a_sealed, "reader", "answer-reader"), (viewer_role, "member", "global-viewer"),
    ):
        grant(row, relation, u(seat) if isinstance(seat, str) else seat)
    q_shared, q_trash = c.ask(round1, seat="peer"), c.ask(round1, seat="peer")
    q_hidden = c.ask(round1, audience="managers")
    manager = u("facilitator")
    with actor_context(manager):
        q_assigned = as_actor(round1, manager).ask("Assigned", "Reply", recipient=u("responder"))
        as_actor(round1, manager).message_post("Kickoff")
        for proposal in (prop_r, prop_p):
            as_actor(proposal, manager).submit()
        for seat in ("retired", "former"):
            as_actor(round1, manager).remove_responder(u(seat))
        as_actor(a_live, manager).set_responder_share(True)
    c.open(round1)
    with system_context(reason="tests.proposals.policy.round1_after"):
        track_task = Task.objects.create(project=prop_r.track, title="Track work")
        trashed_task = Task.objects.create(
            project=prop_r.track, title="Withdrawn work", owner=u("track-owner"), visibility="restricted",
        )
        trashed_task.trash(reason="Withdrawn")
        record_task = Task.objects.create(subject=round1, title="Chase the round")
        q_trash.trash(reason="Withdrawn")
        attachment = ThreadAttachment._base_manager.get(content_type=ContentType.objects.get_for_model(Round))
        # A retired seat that later moderates the round team manages it, but its questions stay withheld.
        Membership.objects.create(group=team, party=Person.objects.for_user(u("former")), role="moderator",
                                  is_confirmed=True)
        type(manager)._base_manager.filter(pk=u("inactive-moderator").pk).update(is_active=False)

    # Round two: a task target, facilitator-only, a status roster and a private published track.
    round2 = c.round(policy="facilitator_only", target_kind="task", roster_visibility="status")
    prop2_r, prop2_p = c.admit(round2, "responder", track=True), c.admit(round2, "peer")
    grant(round2.task, "proposal_viewer", u("task-viewer"))
    with actor_context(manager):
        for proposal in (prop2_r, prop2_p):
            as_actor(proposal, manager).submit()
    c.open(round2)
    with actor_context(manager):
        as_actor(prop2_r, manager).publish_track()
    with system_context(reason="tests.proposals.policy.round2"):
        prop2_r.refresh_from_db()
        shared_task = Task.objects.create(project=prop2_r.track, title="Shared detail")
    with actor_context(manager):
        as_actor(shared_task, manager).set_responder_share(True)

    # Round three: a hidden roster, canceled while both shells are drafts.
    round3 = c.round(roster_visibility="hidden")
    prop3_r = c.admit(round3, "responder")
    prop3_p = c.admit(round3, "peer")
    with actor_context(manager):
        as_actor(round3, manager).cancel()

    specials = {"admin": admin, "anonymous": AnonymousUser(), "service": service}
    offer_fields = ("cost", "currency", "staffing", "confidence", "timeframe_start", "timeframe_end",
                    "valid_until", "source_message", "statement")
    assert_decisions(
        checks=seat_cells(
            lambda seat: specials.get(seat) or u(seat),
            (viewer_role, "effective_member", "global-viewer admin", "outsider"),
            # proposals/round
            (round1, "manage", "facilitator moderator admin", "member inactive-moderator responder"),
            (round1, "evaluate", "evaluator", "facilitator responder"),
            (round1, "respond", "responder drafter", "retired outsider"),
            (round1, "ask", "responder facilitator", "outsider evaluator"),
            (round1, "discuss_clarification", "outsider", "anonymous"),
            (round1, "roster_status", "facilitator", "responder"),
            (round2, "roster_status", "responder peer", "outsider"),
            (round1, "see_roster", "responder", "outsider"),
            (round3, "see_roster", "", "responder"),
            (round1, "receive_track", "responder peer", "drafter retired"),
            (round1, "view_proposals", "round-viewer project-viewer global-viewer admin", "facilitator outsider"),
            (round2, "view_proposals", "task-viewer", "outsider"),
            (round1, "create", "project-owner admin", "facilitator responder"),
            (round2, "create", "project-owner", "responder"),
            (round1, "read", "facilitator moderator member responder drafter reader evaluator requester "
             "round-viewer project-viewer global-viewer service admin",
             "team-viewer retired project-owner project-member outsider anonymous"),
            (round1, "write", "facilitator moderator", "member responder"),
            (round1, "share", "facilitator", "responder"),
            (round1, "delete", "facilitator", "responder"),
            (round1, "read__roster_visibility", "facilitator", "responder"),
            (round1, "read__clarification_askers", "facilitator", "responder"),
            # proposals/topic
            (topic, "read", "responder", "outsider"),
            *((topic, action, "facilitator", "responder") for action in ("create", "write", "delete")),
            # proposals/proposal
            (prop_r, "manage", "facilitator", "responder"),
            (prop_r, "oversee", "evaluator requester proposal-reader", "responder retired"),
            (prop_r, "read_narrowed", "responder", "peer"),
            (prop_r, "peer_read", "peer", "drafter"),
            (prop_r, "read_answers", "peer", "drafter"),
            (prop_r, "read", "responder peer facilitator evaluator requester round-viewer global-viewer service",
             "drafter retired outsider anonymous"),
            (prop_d, "read", "drafter", "peer"),
            (prop2_r, "read", "task-viewer", "peer"),
            (prop_r, "create", "facilitator", "responder"),
            (prop_d, "write", "drafter facilitator", "peer"),
            (prop_r, "write", "facilitator", "responder"),
            (prop3_r, "write", "", "responder"),
            (prop_d, "delete", "drafter", "peer"),
            (prop_r, "share", "facilitator", "responder"),
            (prop_r, "evaluate", "evaluator facilitator", "responder"),
            (prop_r, "publish", "responder facilitator", "peer"),
            (prop3_r, "publish", "", "responder"),
            (prop_r, "withdraw", "responder", "peer"),
            (prop_r, "write_track", "responder", "peer"),
            (prop_r, "read_track", "facilitator responder peer", "outsider"),
            (prop2_r, "read_track", "responder", "peer"),
            (prop2_r, "read_track_status", "peer", "outsider"),
            (prop_r, "read_offer", "responder evaluator requester facilitator", "peer retired"),
            *((prop_r, f"read__{field}", "evaluator", "peer") for field in offer_fields),
            (prop_r, "read__responder", "peer", "outsider"),
            (prop2_r, "read__responder", "peer", "outsider"),
            (prop3_r, "read__responder", "", "peer"),
            # proposals/answer
            (a_round, "manage", "facilitator", "responder"),
            (a_round, "narrow", "responder", "peer"),
            (a_round, "read", "peer evaluator", "drafter"),
            (a_resp, "read", "responder evaluator", "peer"),
            (a_sealed, "read", "facilitator answer-reader", "responder evaluator"),
            (a_live, "read", "peer drafter", "retired"),
            (a_draft, "create", "drafter", "peer"),
            (a_draft, "write", "drafter", "peer"),
            (a_round, "write", "facilitator", "responder"),
            (a_draft, "delete", "drafter", "peer"),
            # proposals/review
            (review, "create", "evaluator", "facilitator"),
            (review, "read", "evaluator facilitator", "responder"),
            (review, "write", "evaluator", "facilitator"),
            (review, "delete", "evaluator", "facilitator"),
            # projects/project through tracks and round targets
            (prop_r.track, "read", "responder peer facilitator", "drafter outsider"),
            (project1, "read", "responder drafter project-owner project-member", "retired member outsider"),
            (prop2_r.track, "read", "responder", "peer"),
            (prop_r.track, "write", "responder facilitator", "peer"),
            (prop_r.track, "share", "facilitator", "responder"),
            # projects/task: record tasks, questions and track tasks
            (record_task, "subject_read", "facilitator", "responder"),
            (record_task, "subject_write", "facilitator", "responder"),
            (q_shared, "read_granted", "responder", "outsider"),
            (q_shared, "read", "responder drafter project-owner project-member facilitator",
             "retired member evaluator outsider"),
            (q_shared, "withheld", "retired former", "responder"),
            (q_shared, "read", "", "former"),
            (q_shared, "share_granted", "facilitator", "responder"),
            (q_shared, "widen", "facilitator project-owner", "responder"),
            (q_shared, "narrow", "peer", "responder"),
            (q_shared, "comment", "peer facilitator", "responder project-owner"),
            (q_shared, "comment_withheld", "project-owner", "responder"),
            (q_shared, "read__clarification_asker", "facilitator peer", "responder"),
            (q_assigned, "write_withheld", "responder", "peer"),
            (q_assigned, "comment", "responder", "peer"),
            (q_hidden, "read", "facilitator", "responder peer"),
            (q_trash, "read", "admin project-owner", "facilitator peer"),
            (track_task, "write", "facilitator responder", "outsider"),
            (track_task, "share", "facilitator", "outsider"),
            (trashed_task, "read", "track-owner admin", "facilitator"),
            (trashed_task, "write", "admin", "facilitator"),
            (trashed_task, "share", "admin", "facilitator"),
            (shared_task, "read", "peer", "outsider"),
            # messaging/thread_attachment: a round's chatter is written by its writers
            (attachment, "target_write", "facilitator", "responder"),
            # Round management never reaches a responder's account.
            (u("responder"), "view_as", "admin", "facilitator"),
        ),
        accessible=(
            (u("responder"), "read", Proposal, (prop_r, prop_p, prop2_r, prop3_r)),
            (u("peer"), "read", Proposal, (prop_p, prop_r, prop2_p, prop3_p)),
            (u("evaluator"), "read", Proposal, (prop_r, prop_p, prop_d, prop_x, prop_former)),
            (u("outsider"), "read", Proposal, ()),
            (u("member"), "read", Round, (round1,)),
            (u("peer"), "read", Answer, (a_round, a_live)),
        ),
    )


def assert_read(row: Any, actor: Any, expected: bool) -> None:
    """The model's point check and its scoped manager agree on one row."""
    with actor_context(actor):
        assert as_actor(row, actor).has_access("read") is expected, (row._meta.label, actor, expected)
        assert type(row).objects.as_user(actor).filter(pk=row.pk).exists() is expected, (row._meta.label, actor)


def test_scoped_managers_and_point_checks_admit_each_seat_its_rows(campaign: ProposalCampaign) -> None:
    """Queryset scope and ``has_access`` enforce the table; a project editor's proposal-viewer grant widens both."""

    c = campaign
    round = c.round()
    own, other = c.admit(round, "responder"), c.admit(round, "peer")
    own_answer, other_answer = c.answer(own), c.answer(other)
    responder, viewer, outsider = c.person("responder"), c.person("viewer"), c.person("outsider")
    for actor, rows in ((responder, {own, own_answer}), (viewer, set()), (outsider, set())):
        for row in (own, other, own_answer, other_answer):
            assert_read(row, actor, row in rows)
    owner = c.person("project-owner")
    with actor_context(owner):
        as_actor(round.project, owner).grant_record_access("proposal_viewer", viewer)
    for row in (own, other, own_answer, other_answer):
        assert_read(row, viewer, True)
    assert not as_actor(own, viewer).has_access("read__cost")


def test_inserts_authorize_the_seat_before_creating_proposals_answers_or_shared_items(
    campaign: ProposalCampaign,
) -> None:
    """The create preflight refuses an unauthorized seat before any row exists, whatever its audience."""

    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder", track=True)
    c.admit(round, "peer")
    manager, responder, peer = c.person("facilitator"), c.person("responder"), c.person("peer")
    with system_context(reason="tests.proposals.insert.topic"):
        topics = [Topic.objects.create(round=round, key=f"t{index}", name="Topic") for index in range(3)]
    with actor_context(peer), pytest.raises(PermissionDenied):
        Proposal.objects.insert(Proposal(round=round, responder=c.person("newcomer")))
    with actor_context(peer), pytest.raises(PermissionDenied):
        Answer.objects.insert(Answer(proposal=proposal, topic=topics[0], visibility="responder"))
    with actor_context(responder), pytest.raises(PermissionDenied, match="manager"):
        Answer(proposal=proposal, topic=topics[0], shared_with_responders=True).sudo(reason="tests.insert").save()
    with actor_context(responder), pytest.raises(PermissionDenied, match="manager"):
        Task(project=proposal.track, title="Shared", shared_with_responders=True).sudo(reason="tests.insert").save()
    assert not Answer._base_manager.filter(proposal=proposal).exists()
    with actor_context(responder):
        narrowed = Answer.objects.insert(Answer(proposal=proposal, topic=topics[1], visibility="responder"))
    with actor_context(manager):
        shared = Answer(proposal=proposal, topic=topics[2], shared_with_responders=True)
        shared.sudo(reason="tests.proposals.insert.manager").save()
    assert Answer._base_manager.get(pk=narrowed.pk).visibility == "responder"
    assert Answer._base_manager.get(pk=shared.pk).shared_with_responders
    c.open(round)
    assert_read(narrowed, peer, False)
    assert_read(shared, peer, True)


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


def test_retirement_cleans_peers_tracks_and_withholds_group_shared_round_content(campaign: ProposalCampaign) -> None:
    """Removing a seat deletes its build grants, reports shared audiences, ends its follow and replays as a no-op."""

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
        round.message_subscribe(user=retiring)
        group = apps.get_model("iam", "Group").objects.create(name=f"{c.prefix}-reviewers")
        peer_task = Task.objects.create(project=peer.track, title="Peer work")
        # Legacy ownership and assignment are deliberately seeded below owning-container defaults.
        Task._base_manager.filter(pk=peer_task.pk).update(owner=retiring, assignee=retiring)
        drive = Drive._base_manager.get(project_bindings__project_id=peer.track_id)
        own_drive = Drive._base_manager.get(project_bindings__project_id=own.track_id)
        file = File.objects.create(drive=drive, filename="Review.txt", storage_path="review.txt", content_hash="a" * 64)
        ordinary = Task.objects.create(project=round.project, title="Unrelated work")
    grant(group, "member", retiring)
    grant(group, "member", c.person("other-member"))
    wildcard = SubjectRef.of("auth/user", "*")
    grant(own_drive, "viewer", wildcard)
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
        with pytest.raises(ValidationError, match="not admitted"):
            as_actor(round, manager).remove_responder(c.person("outsider"))
        report = as_actor(round, manager).remove_responder(retiring)
    removed = {(row["resource"], row["relation"], row["subject"]) for row in report["removed"]}
    for row, relation in track_rows:
        assert (f"{row._meta.rebac_resource_type}:{row.pk}", relation, str(subject)) in removed
        assert_read(row, retiring, False)
        assert_read(row, c.person("other-member"), False)
    for row in (round, own, peer, answer, own_question, shared_question, assigned):
        assert_read(row, retiring, False)
    assert any(row["resource"] == f"proposals/answer:{answer.pk}" for row in report["reported"])
    # A wildcard audience is reported, never deleted: it was not this seat's grant.
    assert {"resource": f"storage/drive:{own_drive.pk}", "relation": "viewer", "subject": str(wildcard)} in report[
        "reported"
    ]
    assert_read(own_drive, c.person("outsider"), True)
    assert not round.message_is_follower(user=retiring)
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


def test_live_responder_share_follows_the_current_roster_and_responders_may_only_clear_it(
    campaign: ProposalCampaign,
) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder", track=True)
    c.admit(round, "peer")
    answer = c.answer(proposal, visibility="responder")
    with system_context(reason="tests.proposals.campaign.shared_task"):
        task = Task.objects.create(project=proposal.track, title="Private detail", visibility="restricted")
        inherited = Task.objects.create(project=proposal.track, title="Own inherited detail")
        drive = Drive.objects.get(project_bindings__project_id=proposal.track_id)
        document = File.objects.create(
            drive=drive,
            filename="Private.txt",
            content_hash="c" * 64,
            storage_path="private.txt",
            visibility="record",
        )
        FileAttachment.objects.attach(document, task)
    manager, responder = c.person("facilitator"), c.person("responder")
    before = active_relationship_model().objects.count()
    items = ((task, manager), (inherited, responder), (answer, responder))
    with actor_context(manager):
        for item, _clearer in items:
            as_actor(item, manager).set_responder_share(True, expected_revision=item.revision)
    assert active_relationship_model().objects.count() == before
    c.admit(round, "late")
    for seat in ("peer", "late"):
        assert_read(document, c.person(seat), True)
    for item, _clearer in items:
        for seat in ("peer", "late"):
            assert_read(item, c.person(seat), True)
        with actor_context(responder), pytest.raises(PermissionDenied):
            as_actor(item, responder).set_responder_share(True)
    with actor_context(manager):
        as_actor(round, manager).remove_responder(c.person("peer"))
    assert_read(document, c.person("peer"), False)
    for item, clearer in items:
        assert_read(item, c.person("peer"), False)
        assert_read(item, c.person("late"), True)
        with actor_context(clearer):
            as_actor(item, clearer).set_responder_share(False)
        assert_read(item, c.person("late"), False)
    assert_read(document, c.person("late"), False)


def test_answer_audience_narrows_for_its_responder_and_widens_only_for_managers(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    answer = c.answer(c.admit(round, "responder"))
    c.admit(round, "peer")
    manager, responder, peer = c.person("facilitator"), c.person("responder"), c.person("peer")
    with actor_context(peer), pytest.raises(PermissionDenied):
        as_actor(answer, peer).set_visibility("sealed")
    with actor_context(responder):
        as_actor(answer, responder).set_visibility("responder")
        with pytest.raises(PermissionDenied):
            as_actor(answer, responder).set_visibility("round")
        as_actor(answer, responder).set_visibility("sealed")
        with pytest.raises(PermissionDenied):
            as_actor(answer, responder).set_visibility("responder")
    assert_read(answer, responder, False)
    with actor_context(manager):
        as_actor(answer, manager).set_visibility("round")
    assert Answer._base_manager.get(pk=answer.pk).visibility == "round"


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


def test_requester_ceiling_refuses_every_build_share_without_writing_a_tuple(campaign: ProposalCampaign) -> None:
    """The requester never holds build content, as a user, through a group or through a wildcard."""

    c = campaign
    requester = c.person("requester")
    with system_context(reason="tests.proposals.campaign.ceiling"):
        party = Person.objects.for_user(requester)
        group = apps.get_model("iam", "Group").objects.create(name=f"{c.prefix}-requesters")
    grant(group, "member", requester)
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
        for subject in (
            to_subject_ref(requester),
            SubjectRef.of("auth/group", str(group.pk), "member"),
            SubjectRef.of("auth/user", "*"),
        ):
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


def test_task_holder_ceiling_asks_the_round_only_for_track_items(
    campaign: ProposalCampaign, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A track task keeps the requester out through its round; a question on the
    round's target project never consults that project's own holder policy."""

    c = campaign
    requester = c.person("requester")
    with system_context(reason="tests.proposals.ceiling.requester"):
        party = Person.objects.for_user(requester)
    round = c.round(requester_party=party)
    proposal = c.admit(round, "responder", track=True)
    c.admit(round, "asker")
    with system_context(reason="tests.proposals.ceiling.track_task"):
        track_task = Task.objects.create(project=proposal.track, title="Build detail")
    question = c.ask(round, seat="asker")

    def forbidden(self: Any, relation: str, subject: Any) -> None:
        pytest.fail("A task outside a track must not consult its project's holder policy.")

    monkeypatch.setattr(Project, "validate_record_access_subject", forbidden)
    manager = c.person("facilitator")
    with actor_context(manager):
        with pytest.raises(RecordAccessSubjectRefused):
            as_actor(track_task, manager).grant_record_access("reader", requester)
        passed = as_actor(round, manager).pass_clarification(question, c.person("responder"))
        as_actor(question, manager).grant_record_access("reader", requester)
    assert passed.assignee_id == c.person("responder").pk
    assert_read(track_task, requester, False)
    assert_read(question, requester, True)


def test_requester_cannot_enter_through_admission_shell_insert_or_reassignment(campaign: ProposalCampaign) -> None:
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
    assert c.admit(c.round(), "requester").responder_id == requester.pk
    other = c.round()
    c.admit(other, "responder")
    with system_context(reason="tests.proposals.requester.reverse_admission"):
        other.requester_party = Person.objects.for_user(c.person("responder"))
        with pytest.raises(RecordAccessSubjectRefused):
            other.save(update_fields=("requester_party",))
    assert Round._base_manager.get(pk=other.pk).requester_party_id is None


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


def test_responder_and_requester_are_facts_of_the_shell_and_party_never_grantable(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    manager = c.person("facilitator")
    with actor_context(manager):
        for relation in ("responder", "requester"):
            with pytest.raises(ValueError, match="grantable"):
                as_actor(round, manager).grant_record_access(relation, c.person("requester"))
    assert not active_relationship_model().objects.filter(
        resource_type="proposals/round", resource_id=str(round.pk), relation__in=("responder", "requester"),
    ).exists()


@pytest.mark.parametrize("verb", ("submit", "create_track"))
def test_write_verbs_recheck_retirement_after_preflight_with_the_pinned_actor(
    campaign: ProposalCampaign, monkeypatch: pytest.MonkeyPatch, verb: str,
) -> None:
    """A seat retired between the preflight and the lock is refused, whatever the ambient actor holds."""

    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder")
    responder, manager = c.person("responder"), c.person("facilitator")
    before = Project._base_manager.count()
    has_access = Proposal.has_access
    retired = False

    def retire_after_preflight(instance: Proposal, action: str, **kwargs: Any) -> bool:
        nonlocal retired
        allowed = has_access(instance, action, **kwargs)
        if instance.pk == proposal.pk and action == "write" and not retired:
            assert allowed
            retired = True
            with actor_context(manager):
                as_actor(round, manager).remove_responder(responder)
        return allowed

    monkeypatch.setattr(Proposal, "has_access", retire_after_preflight)
    with actor_context(manager), pytest.raises(PermissionDenied, match="Proposal write access"):
        getattr(as_actor(proposal, responder), verb)()
    stored = Proposal._base_manager.get(pk=proposal.pk)
    assert retired
    assert stored.retired_at == c.now
    assert stored.state == "draft"
    assert stored.submitted_at is None
    assert stored.submitted_by_id is None
    assert stored.track_id is None
    assert Project._base_manager.count() == before
