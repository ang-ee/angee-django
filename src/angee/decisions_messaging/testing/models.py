"""Concrete decision mail ledgers for isolated tests."""

from angee.decisions_messaging import models as sources


class DecisionNotice(sources.DecisionNotice):
    class Meta(sources.DecisionNotice.Meta):
        abstract = False
        app_label = "decisions_messaging"
        db_table = "test_decisions_messaging_decision_notice"


class DigestNotice(sources.DigestNotice):
    class Meta(sources.DigestNotice.Meta):
        abstract = False
        app_label = "decisions_messaging"
        db_table = "test_decisions_messaging_digest_notice"
