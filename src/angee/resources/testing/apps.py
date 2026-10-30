"""Django app identity for the optional resource test composition."""

from django.apps import AppConfig


class ResourcesTestingConfig(AppConfig):
    """Load the shared concrete ledger after its source addon."""

    name = "angee.resources.testing"
    label = "resources_testing"
