"""Django app identity for the workflows addon."""

from typing import Any

from django.apps import AppConfig, apps
from django.core import checks
from django.db import transaction
from django.db.models.signals import class_prepared, post_delete, pre_delete
from rebac import system_context
from rebac.resources import model_resource_type

from angee.decisions.signals import decision_answered
from angee.iam.service_users import deactivate_service_user
from angee.workflows.states import RunStatus


def wake_decision(sender: Any, *, decision: Any, **kwargs: Any) -> None:
    """Let the workflow lock owner enqueue settled decision waiters after commit."""
    from angee.workflows.runner import runner

    transaction.on_commit(lambda: runner.wake_decisions(decision.pk), robust=True)


def deactivate_workflow_principal(sender: Any, *, instance: Any, **kwargs: Any) -> None:
    """Deactivate the linked user after any workflow deletion path."""

    deactivate_service_user(instance)


def revoke_deleted_trigger_grants(sender: Any, *, instance: Any, **kwargs: Any) -> None:
    """Release a trigger's tuples on instance and queryset deletion alike."""

    sender.objects.lock_grants(instance.workflow_id)
    sender.objects.reconcile_grants(instance, ())


def stop_deleted_record_runs(sender: Any, *, instance: Any, **kwargs: Any) -> None:
    """Deletion leaves retained identities, but no run continues asking about them."""
    if sender._meta.app_label in {"workflows", "decisions"} or not model_resource_type(sender):
        return
    model = apps.get_model("workflows", "WorkflowRun")
    with system_context(reason="workflows.deleted_record"):
        runs = model.objects.exclude(status__in=RunStatus.terminal_values()).about(
            instance, operations=("created", "changed"), ancestors=False,
        )
        for run in runs.select_related("run_as"):
            model.objects.cancel_on_commit(run, run.run_as)


def connect_record_deletion(sender: Any, **kwargs: Any) -> None:
    if sender._meta.app_label not in {"workflows", "decisions"} and model_resource_type(sender):
        pre_delete.connect(stop_deleted_record_runs, sender=sender,
                           dispatch_uid=f"workflows.deleted_record.{sender._meta.label_lower}")


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
        decision_answered.connect(wake_decision, dispatch_uid="workflows.decision_answered")
        for model in apps.get_models():
            connect_record_deletion(model)
        class_prepared.connect(connect_record_deletion, dispatch_uid="workflows.record_deletion.connect")
        post_delete.connect(
            deactivate_workflow_principal, sender=apps.get_model("workflows", "Workflow"),
            dispatch_uid="workflows.service_user.deactivate",
        )
        pre_delete.connect(
            revoke_deleted_trigger_grants, sender=apps.get_model("workflows", "Trigger"),
            dispatch_uid="workflows.trigger_grants.revoke",
        )
        TriggerSource.connect_registered()
