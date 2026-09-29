"""Django app identity for the workflows addon."""

from typing import Any

from django.apps import AppConfig, apps

from angee.decisions.signals import decision_group_settled


def wake_review(sender: Any, *, group: Any, **kwargs: Any) -> None:
    """Let the workflow lock owner enqueue settled decision waiters after commit."""
    apps.get_model("workflows", "StepRun").objects.wake_decisions(group.pk)


class WorkflowsConfig(AppConfig):
    """Register workflow source models without the retired engine hooks."""

    default = True
    name = "angee.workflows"

    def ready(self) -> None:
        """Subscribe the waiter owner to the decisions lifecycle."""
        decision_group_settled.connect(wake_review, dispatch_uid="workflows.review_settled")
