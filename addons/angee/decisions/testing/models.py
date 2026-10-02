"""One concrete test model per decision resource, registered from conftest."""

from angee.decisions import models as sources
from angee.intake.models import DecisionIntake
from angee.workflows.models import DecisionWorkflow


class DecisionGroup(sources.DecisionGroup):
    """Concrete group retained by reviews and independent decision tests."""

    class Meta(sources.DecisionGroup.Meta):
        """Keep native source options on the isolated group table."""

        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_group"
        rebac_resource_type = "decisions/group"


class Decision(DecisionIntake, DecisionWorkflow, sources.Decision):
    """Concrete seat with the installed intake and workflow contributions."""

    class Meta(sources.Decision.Meta):
        """Keep native source options on the isolated decision table."""

        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_decision"
        rebac_resource_type = "decisions/decision"


class DecisionEvidence(sources.DecisionEvidence):
    """Concrete evidence projection for source tests."""

    class Meta(sources.DecisionEvidence.Meta):
        """Keep native source options on the isolated evidence table."""

        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_evidence"
        rebac_resource_type = "decisions/evidence"
