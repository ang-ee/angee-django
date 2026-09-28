"""Native test app identity."""

from django.apps import AppConfig


class DecisionsTestingConfig(AppConfig):
    """Register concrete source-test tables without composing a host."""

    name = "angee.decisions.testing"
    label = "decisions_testing"
