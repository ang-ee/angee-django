"""Django app identity for the optional integrate test composition."""

from django.apps import AppConfig


class IntegrateTestingConfig(AppConfig):
    """Load the shared concrete models after their source addon."""

    name = "angee.integrate.testing"
    label = "integrate_testing"
