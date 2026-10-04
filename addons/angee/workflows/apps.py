"""Django app identity for the workflows addon."""

from typing import Any

from django.apps import AppConfig, apps
from django.core import checks
from django.db import transaction
from django.db.models.signals import post_delete, pre_delete
from rebac import system_context
from rebac.resources import model_resource_type

from angee.decisions.signals import decision_answered
from angee.iam.service_users import deactivate_service_user


def wake_review(sender: Any, *, decision: Any, **kwargs: Any) -> None:
    """Let the workflow lock owner enqueue settled decision waiters after commit."""
    from angee.workflows.runner import runner

    transaction.on_commit(lambda: runner.wake_decisions(decision.pk))


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
        ids = list(model.objects.about(instance).values_list("pk", flat=True))
    if not ids:
        return

    def stop() -> None:
        with system_context(reason="workflows.deleted_record.stop"):
            for run in model.objects.filter(pk__in=ids).order_by("pk"):
                model.objects.cancel(run, actor=run.run_as)
    transaction.on_commit(stop)


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
        pre_delete.connect(stop_deleted_record_runs, dispatch_uid="workflows.deleted_record")
        post_delete.connect(
            deactivate_workflow_principal, sender=apps.get_model("workflows", "Workflow"),
            dispatch_uid="workflows.service_user.deactivate",
        )
        pre_delete.connect(
            revoke_deleted_trigger_grants, sender=apps.get_model("workflows", "Trigger"),
            dispatch_uid="workflows.trigger_grants.revoke",
        )
        TriggerSource.connect_registered()
