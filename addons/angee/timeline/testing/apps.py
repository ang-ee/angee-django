"""Django identity for the optional timeline test composition."""

from django.apps import AppConfig


class TimelineTestingConfig(AppConfig):
    """Load the concrete ledger after its source addon."""

    name = "angee.timeline.testing"
    label = "timeline_testing"
