"""App identity for the bridge execution composition."""

from django.apps import AppConfig


class WorkflowsIntegrateConfig(AppConfig):
    """No models or runtime registration: declarations compose through settings."""

    default = True
    name = "angee.workflows_integrate"
