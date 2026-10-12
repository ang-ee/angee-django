"""Native Django deletion dispatch for retained milestone receipts."""

from typing import Any

from django.db import models
from django.db.models.signals import pre_delete, pre_migrate

from angee.base.signals import connect_for_models
from angee.projects.models import Milestone


def connect() -> None:
    """Bind the receipt rule to the milestone models only, and the record-task cutover's refusal."""
    from django.apps import apps

    from angee.projects.upgrade import refuse_incomplete_cutover

    connect_for_models(pre_delete, retain_milestone_receipt,
                       applies=lambda model: issubclass(model, Milestone),
                       dispatch_uid="projects.milestone.retain_receipt")
    pre_migrate.connect(refuse_incomplete_cutover, sender=apps.get_app_config("projects"),
                        dispatch_uid="projects.record_tasks.refuse_incomplete_cutover")


def retain_milestone_receipt(
    instance: Any,
    origin: models.Model | models.QuerySet[Any] | None = None,
    **kwargs: Any,
) -> None:
    """Apply the milestone owner's rule to instance, queryset and cascade deletion."""

    instance.validate_deletion(origin=origin)
