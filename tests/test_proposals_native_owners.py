"""Canonical proposal owners preserve historical imports and native manager binding."""

from angee.proposals import choices, managers, models, querysets
from tests.proposals_models import Proposal, Round


def test_historical_model_imports_resolve_to_canonical_native_owners():
    assert models.RoundManager is managers.RoundManager
    assert models.ProposalManager is managers.ProposalManager
    assert models.RoundQuerySet is querysets.RoundQuerySet
    assert models._RoundBaseManager is managers._RoundBaseManager
    for name in (
        "RoundOpeningPolicy", "RoundStatus", "RoundOutcome", "ProposalState", "ProposalConfidence",
        "RosterVisibility", "ClarificationAskers", "AnswerVisibility", "QuestionAudience", "PassAudience",
        "OPENING_POLICY_ORDER", "ANSWER_VISIBILITY_ORDER",
    ):
        assert getattr(models, name) is getattr(choices, name)


def test_concrete_proposal_managers_bind_the_existing_guarded_querysets():
    assert isinstance(Round._default_manager, managers.RoundManager)
    assert isinstance(Round._default_manager.get_queryset(), querysets.RoundQuerySet)
    assert isinstance(Round._base_manager.get_queryset(), querysets._RoundBaseQuerySet)
    assert isinstance(Proposal._default_manager, managers.ProposalManager)
    assert Round._default_manager.model is Round
    assert Proposal._default_manager.model is Proposal
