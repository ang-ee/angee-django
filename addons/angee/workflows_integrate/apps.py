"""Django application for integration workflow declarations."""

from django.apps import AppConfig


class WorkflowsIntegrateConfig(AppConfig):
    """Register the integration workflow satellite."""

    name = "angee.workflows_integrate"
