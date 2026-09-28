"""Django identity of the extraction addon."""

from django.apps import AppConfig


class WorkflowsExtractionConfig(AppConfig):
    """Register immutable extraction source models."""

    name = "angee.workflows_extraction"
