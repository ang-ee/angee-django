"""Concrete proposal models bind their native guarded managers."""

from angee.proposals import managers, querysets
from tests.proposals_models import Proposal, Round


def test_concrete_proposal_managers_bind_the_existing_guarded_querysets():
    assert isinstance(Round._default_manager, managers.RoundManager)
    assert isinstance(Round._default_manager.get_queryset(), querysets.RoundQuerySet)
    assert isinstance(Round._base_manager.get_queryset(), querysets._RoundBaseQuerySet)
    assert isinstance(Proposal._default_manager, managers.ProposalManager)
    assert Round._default_manager.model is Round
    assert Proposal._default_manager.model is Proposal
