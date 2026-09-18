from django.apps import AppConfig


class WorkflowsOcrHistoryConfig(AppConfig):
    """Load released workflows_ocr migration history without serving models."""

    default = True
    name = "angee.workflows_ocr"
    verbose_name = "Workflows extraction migration history"
    angee_runtime_migration_history = True
