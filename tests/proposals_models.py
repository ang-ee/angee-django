"""Concrete proposal models for the bare-Django authorization harness."""

from angee.proposals.models import Answer as AbstractAnswer
from angee.proposals.models import Proposal as AbstractProposal
from angee.proposals.models import ProposalsRole as AbstractProposalsRole
from angee.proposals.models import Review as AbstractReview
from angee.proposals.models import Round as AbstractRound
from angee.proposals.models import Topic as AbstractTopic
from tests import money_models  # noqa: F401 -- register Proposal.currency's concrete target


class Round(AbstractRound):
    """Concrete solicitation Round used by access tests."""

    class Meta(AbstractRound.Meta):
        abstract = False
        app_label = "proposals"
        db_table = "test_proposals_round"
        rebac_resource_type = "proposals/round"


class Topic(AbstractTopic):
    """Concrete Round topic used by access tests."""

    class Meta(AbstractTopic.Meta):
        abstract = False
        app_label = "proposals"
        db_table = "test_proposals_topic"
        rebac_resource_type = "proposals/topic"


class Proposal(AbstractProposal):
    """Concrete sealed proposal used by access tests."""

    class Meta(AbstractProposal.Meta):
        abstract = False
        app_label = "proposals"
        db_table = "test_proposals_proposal"
        rebac_resource_type = "proposals/proposal"


class Answer(AbstractAnswer):
    """Concrete answer used by access tests."""

    class Meta(AbstractAnswer.Meta):
        abstract = False
        app_label = "proposals"
        db_table = "test_proposals_answer"
        rebac_resource_type = "proposals/answer"


class Review(AbstractReview):
    """Concrete evaluator review used by access tests."""

    class Meta(AbstractReview.Meta):
        abstract = False
        app_label = "proposals"
        db_table = "test_proposals_review"
        rebac_resource_type = "proposals/review"


class ProposalsRole(AbstractProposalsRole):  # type: ignore[misc, valid-type]
    """Concrete tableless anchor for the proposals role namespace."""

    class Meta:
        abstract = False
        managed = False
        app_label = "proposals"
        rebac_resource_type = "proposals/role"
