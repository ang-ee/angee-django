from django.apps import AppConfig
from django.core import checks

from angee.workflows_extraction.checks import check_extraction_settings


class WorkflowsExtractionConfig(AppConfig):
    """Django application for immutable document extraction evidence."""

    default = True
    name = "angee.workflows_extraction"

    def ready(self) -> None:
        """Register extraction configuration checks after app population."""

        super().ready()
        checks.register(check_extraction_settings)
