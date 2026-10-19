"""The concrete ledger used by source-addon tests."""

from angee.timeline import models as sources


class Event(sources.Event):
    class Meta(sources.Event.Meta):
        abstract = False
        app_label = "timeline"
        db_table = "test_timeline_event"
