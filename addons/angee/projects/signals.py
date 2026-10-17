"""Native Django deletion dispatch for retained milestone receipts and record tasks."""

from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.signals import pre_delete, pre_migrate
from rebac import generic_target, system_context

from angee.base.scoping import system_queryset
from angee.base.signals import connect_for_models
from angee.messaging.models import ThreadedModelMixin
from angee.projects.models import Milestone, Task
from angee.projects.upgrade import GUARD_DISPATCH_UID, refuse_incomplete_cutover


def connect() -> None:
    """Bind the receipt rule to milestones, the task teardown to subject records, and the cutover's refusal."""
    connect_for_models(pre_delete, retain_milestone_receipt,
                       applies=lambda model: issubclass(model, Milestone),
                       dispatch_uid="projects.milestone.retain_receipt")
    connect_for_models(pre_delete, teardown_record_tasks,
                       applies=takes_record_tasks,
                       dispatch_uid="projects.record_tasks.teardown")
    pre_migrate.connect(refuse_incomplete_cutover, sender=apps.get_app_config("projects"),
                        dispatch_uid=GUARD_DISPATCH_UID)


def takes_record_tasks(model: type[models.Model]) -> bool:
    """Whether rows of ``model`` can be a task's subject: a declared target, or a chatter host.

    A chatter host already pays the per-row delete that a receiver costs, and a
    test host declares its subjects after the schema it reads them from loads.
    """

    if issubclass(model, ThreadedModelMixin):
        return True
    try:
        # The declaration lives on the task's schema; startup reads it from the
        # source class, never through a composed-model lookup.
        return Task.declares_target(model)
    except ImproperlyConfigured:
        return False


def retain_milestone_receipt(
    instance: Any,
    origin: models.Model | models.QuerySet[Any] | None = None,
    **kwargs: Any,
) -> None:
    """Apply the milestone owner's rule to instance, queryset and cascade deletion."""

    instance.validate_deletion(origin=origin)


def teardown_record_tasks(sender: Any, instance: Any, **kwargs: Any) -> None:
    """A deleted record takes the tasks about it: a subject pointer has no database cascade."""

    del sender, kwargs
    if instance.pk is None:
        return
    task = apps.get_model("projects", "Task")
    try:
        lookups = generic_target(instance).lookups(task, "subject")
    except ValueError:
        # A row no polymorphic edge can name carries no tasks.
        return
    with system_context(reason="projects.record_tasks.teardown"):
        system_queryset(task).using(instance._state.db).filter(**lookups).delete()
