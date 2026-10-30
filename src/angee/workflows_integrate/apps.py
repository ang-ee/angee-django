"""Django application for integration workflow declarations."""

from django.apps import AppConfig
from django.core import checks


class WorkflowsIntegrateConfig(AppConfig):
    """Register the integration workflow satellite."""

    name = "angee.workflows_integrate"

    def ready(self) -> None:
        super().ready()
        from angee.workflows_integrate.archive_steps import check_archive_extractor_registry

        checks.register(check_archive_extractor_registry, checks.Tags.models)
