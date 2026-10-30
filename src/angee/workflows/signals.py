"""Workflow lifecycle receivers and transactional run cleanup."""

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from asgiref.sync import iscoroutinefunction
from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_delete, pre_delete
from django.dispatch import Signal
from rebac import system_context
from rebac.resources import model_resource_type

from angee.base.signals import connect_for_models
from angee.decisions.signals import decision_answered
from angee.iam.service_users import deactivate_service_user

logger = logging.getLogger(__name__)


def wake_decision(sender: Any, *, decision: Any, **kwargs: Any) -> None:
    """Dispatch settled decisions after commit, once the runner's model owners exist."""
    # The runner composes managers that import this module's run_stopping signal.
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

    model = apps.get_model("workflows", "WorkflowRun")
    with system_context(reason="workflows.deleted_record"):
        for run in model.objects.working_on(instance).select_related("run_as"):
            model.objects.cancel_on_commit(run, run.run_as)


def connect() -> None:
    """Connect each lifecycle receiver only to the models it concerns."""

    decision_answered.connect(wake_decision, dispatch_uid="workflows.decision_answered")
    connect_for_models(
        pre_delete,
        stop_deleted_record_runs,
        applies=lambda model: model._meta.app_label not in {"workflows", "decisions"}
        and bool(model_resource_type(model)),
        dispatch_uid="workflows.deleted_record",
    )
    post_delete.connect(
        deactivate_workflow_principal, sender=apps.get_model("workflows", "Workflow"),
        dispatch_uid="workflows.service_user.deactivate",
    )
    pre_delete.connect(
        revoke_deleted_trigger_grants, sender=apps.get_model("workflows", "Trigger"),
        dispatch_uid="workflows.trigger_grants.revoke",
    )


def isolated_run_cleanup[**P, R](receiver: Callable[P, R]) -> Callable[P, R | Exception]:
    """Wrap a synchronous cleanup receiver in its own savepoint before connecting it."""
    if iscoroutinefunction(receiver):
        raise TypeError("Run cleanup receivers must be synchronous to share the terminal transaction.")

    @wraps(receiver)
    def receive(*args: P.args, **kwargs: P.kwargs) -> R | Exception:
        try:
            with transaction.atomic():
                return receiver(*args, **kwargs)
        except Exception as error:
            logger.exception("Workflow cleanup receiver %r failed.", receiver)
            return error

    return receive


run_stopping = Signal()
"""Release run-owned resources once on each transition from open to terminal.

Dispatch uses ``send_robust`` inside a savepoint owned by the terminal writer,
so no receiver can abort the terminal write. Every receiver must be decorated
with ``isolated_run_cleanup`` before connecting through Django's public Signal
API. Receivers perform synchronous, database-only, idempotent cleanup in the
terminal writer's transaction; each has its own savepoint. A failure rolls back
that receiver, is logged, and lets later receivers finish. An undecorated
receiver that leaves the transaction unusable rolls back every receiver's
cleanup, still without blocking the terminal write.
Retried runs can reach another terminal transition.
The run instance may still carry its previous status. Deferred cancellation only
enqueues work: cleanup waits until the cancellation task owns the run locks.
"""
