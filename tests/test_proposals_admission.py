"""Direct calls to the proposal capture and identify verbs enforce the GraphQL edge's admission."""

import pytest
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref

from angee.messaging.testing.models import Message, Person
from tests.proposals_campaign import as_actor, grant
from tests.proposals_campaign import campaign as campaign
from tests.proposals_campaign import campaign_schema as campaign_schema
from tests.proposals_models import Answer, Proposal
from tests.t3_campaign import relationship_snapshot


def private_inputs(campaign):
    """Create a party and a message that no campaign seat can read yet."""
    with system_context(reason="tests.proposals.admission.setup"):
        party = Person.objects.for_user(campaign.person("private-party"))
        message = Message.objects.create(
            platform="email",
            metadata={"proposal_capture": {"staffing": "A complete response"}},
        )
    return party, message


def test_identify_requires_proposal_write_and_party_read(campaign):
    proposal = campaign.admit(campaign.round(), "responder")
    party, _message = private_inputs(campaign)
    outsider, responder = campaign.person("outsider"), campaign.person("responder")
    for actor in (outsider, responder):
        with actor_context(actor):
            target, related = as_actor(proposal, actor), as_actor(party, actor)
            assert target.has_access("write") is (actor == responder)
            assert not related.has_access("read")
            grants = relationship_snapshot()
            with pytest.raises(PermissionDenied):
                target.identify_party(related)
            assert relationship_snapshot() == grants
            assert Proposal._base_manager.get(pk=proposal.pk).party_id is None
    grant(party, "reader", responder)
    with actor_context(outsider):
        # The proposal's pinned requester wins over this unrelated ambient actor.
        target = as_actor(proposal, responder)
        target.identify_party(as_actor(party, outsider))
        target.identify_party(as_actor(party, outsider))
    assert Proposal._base_manager.get(pk=proposal.pk).party_id == party.pk
    assert target.actor() == to_subject_ref(responder)


def test_capture_checks_round_message_and_party_before_any_write(campaign):
    round = campaign.round()
    party, message = private_inputs(campaign)
    outsider, manager = campaign.person("outsider"), campaign.person("facilitator")
    baseline = (Proposal._base_manager.count(), Answer._base_manager.count())
    for actor in (outsider, manager):
        with actor_context(actor):
            target, source, related = (as_actor(row, actor) for row in (round, message, party))
            assert target.has_access("write") is (actor == manager)
            assert not source.has_access("read")
            grants = relationship_snapshot()
            with pytest.raises(PermissionDenied):
                Proposal.objects.capture_from_message(source, target, related)
            assert relationship_snapshot() == grants
        assert (Proposal._base_manager.count(), Answer._base_manager.count()) == baseline
    grant(message, "reader", manager)
    with actor_context(manager):
        assert as_actor(message, manager).has_access("read")
        assert not as_actor(party, manager).has_access("read")
        grants = relationship_snapshot()
        with pytest.raises(PermissionDenied):
            Proposal.objects.capture_from_message(
                as_actor(message, manager),
                as_actor(round, manager),
                as_actor(party, manager),
            )
        assert relationship_snapshot() == grants
    assert (Proposal._base_manager.count(), Answer._base_manager.count()) == baseline
    grant(party, "reader", manager)
    with actor_context(outsider):
        captured = Proposal.objects.capture_from_message(
            as_actor(message, outsider),
            as_actor(round, manager),
            as_actor(party, outsider),
        )
        replay = Proposal.objects.capture_from_message(
            as_actor(message, outsider),
            as_actor(round, manager),
            as_actor(party, outsider),
        )
    assert replay.pk == captured.pk
    assert captured.state == "submitted" and captured.source_message_id == message.pk
    assert captured.submitted_by_id == manager.pk
    assert not captured.is_sudo()


def test_trusted_system_context_admits_actorless_identify_and_capture(campaign):
    proposal = campaign.admit(campaign.round(), "responder")
    capture_round = campaign.round()
    party, message = private_inputs(campaign)
    with system_context(reason="tests.proposals.admission.trusted"):
        target = Proposal._base_manager.get(pk=proposal.pk)
        related = type(party)._base_manager.get(pk=party.pk)
        target.identify_party(related)
        captured = Proposal.objects.capture_from_message(
            Message._base_manager.get(pk=message.pk),
            type(capture_round)._base_manager.get(pk=capture_round.pk),
            related,
        )
    assert Proposal._base_manager.get(pk=proposal.pk).party_id == party.pk
    assert captured.state == "submitted"
    assert captured.actor() is None and not captured.is_sudo()
