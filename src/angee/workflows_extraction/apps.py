"""Django identity of the extraction addon."""

from django.apps import AppConfig


class WorkflowsExtractionConfig(AppConfig):
    """Register the workflow adapter for extraction."""

    name = "angee.workflows_extraction"
