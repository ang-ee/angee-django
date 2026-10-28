"""Django app identity for the workflows addon."""

from django.apps import AppConfig
from django.core import checks


class WorkflowsConfig(AppConfig):
    """Connect workflow admission, observation and record lifecycle hooks."""

    default = True
    name = "angee.workflows"

    def ready(self) -> None:
        """Connect native sources alongside decision and deletion hooks."""
        from angee.workflows.signals import connect
        from angee.workflows.steps import check_step_registry
        from angee.workflows.subjects import check_run_subject_models
        from angee.workflows.triggers import TriggerSource, check_record_changed_models

        checks.register(check_step_registry, checks.Tags.models)
        checks.register(check_record_changed_models, checks.Tags.models)
        checks.register(check_run_subject_models, checks.Tags.models)
        connect()
        TriggerSource.connect_registered()
