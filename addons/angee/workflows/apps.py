"""Django app identity for the workflows addon."""

from django.apps import AppConfig


class WorkflowsConfig(AppConfig):
    """Register workflow source models without the retired engine hooks."""

    default = True
    name = "angee.workflows"
