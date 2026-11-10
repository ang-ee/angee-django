"""Party method admission for the identity review workflow."""

from django_compose.models import Composable

from angee.decisions.mixins import DecisionTargetMixin


class PartyDecisionTarget(DecisionTargetMixin, Composable, extends="parties.Party"):
    """Allow identity proposals to invoke the party's existing write owner."""

    decision_methods = ("apply_identity",)

    class Meta:
        abstract = True
