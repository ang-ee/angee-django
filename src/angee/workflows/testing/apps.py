"""Django app identity for the optional workflows test composition."""

from django.apps import AppConfig


class WorkflowsTestingConfig(AppConfig):
    """Load the shared concrete models after their source addon."""

    name = "angee.workflows.testing"
    label = "workflows_testing"
