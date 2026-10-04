"""Django app identity for the workflows addon."""

from typing import Any

from django.apps import AppConfig, apps
from django.core import checks
from django.db.models.signals import post_delete, pre_delete

from angee.decisions.signals import decision_answered
from angee.iam.service_users import deactivate_service_user


def wake_review(sender: Any, *, decision: Any, **kwargs: Any) -> None:
    """Let the workflow lock owner enqueue settled decision waiters after commit."""
    from angee.workflows.runner import runner

    runner.wake_decisions(decision.pk)


def deactivate_workflow_principal(sender: Any, *, instance: Any, **kwargs: Any) -> None:
    """Deactivate the linked user after any workflow deletion path."""

    deactivate_service_user(instance)


def revoke_deleted_trigger_grants(sender: Any, *, instance: Any, **kwargs: Any) -> None:
    """Release a trigger's tuples on instance and queryset deletion alike."""

    sender.objects.lock_grants(instance.workflow_id)
    sender.objects.reconcile_grants(instance, ())


class WorkflowsConfig(AppConfig):
    """Register workflow source models without the retired engine hooks."""

    default = True
    name = "angee.workflows"

    def ready(self) -> None:
        """Subscribe the waiter owner to the decisions lifecycle."""
        from angee.workflows.subjects import check_run_subject_models
        from angee.workflows.triggers import TriggerSource, check_record_changed_models

        checks.register(check_record_changed_models, checks.Tags.models)
        checks.register(check_run_subject_models, checks.Tags.models)
        decision_answered.connect(wake_review, dispatch_uid="workflows.decision_answered")
        post_delete.connect(
            deactivate_workflow_principal, sender=apps.get_model("workflows", "Workflow"),
            dispatch_uid="workflows.service_user.deactivate",
        )
        pre_delete.connect(
            revoke_deleted_trigger_grants, sender=apps.get_model("workflows", "Trigger"),
            dispatch_uid="workflows.trigger_grants.revoke",
        )
        TriggerSource.connect_registered()
