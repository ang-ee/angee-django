"""Concrete decision resources for isolated tests."""

from angee.decisions import models as sources


class Decision(sources.Decision):
    class Meta(sources.Decision.Meta):
        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_decision"
        rebac_resource_type = "decisions/decision"


class DecisionRecord(sources.DecisionRecord):
    class Meta(sources.DecisionRecord.Meta):
        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_record"
        rebac_resource_type = "decisions/record"
