"""Concrete decision tables for source-addon tests."""

from angee.decisions import models as sources


class DecisionGroup(sources.DecisionGroup):
    """Concrete source-test group."""

    class Meta(sources.DecisionGroup.Meta):
        """Preserve group contracts under an isolated test table name."""

        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_group"
        rebac_resource_type = "decisions/group"


class Decision(sources.Decision):
    """Concrete source-test seat."""

    class Meta(sources.Decision.Meta):
        """Preserve seat contracts under an isolated test table name."""

        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_decision"
        rebac_resource_type = "decisions/decision"


class DecisionEvidence(sources.DecisionEvidence):
    """Concrete source-test evidence projection."""

    class Meta(sources.DecisionEvidence.Meta):
        """Preserve evidence contracts under an isolated test table name."""

        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_evidence"
        rebac_resource_type = "decisions/evidence"
