"""Native Django deletion dispatch for retained milestone receipts."""

from typing import Any

from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from angee.projects.models import Milestone


@receiver(pre_delete, dispatch_uid="projects.milestone.retain_receipt")
def retain_milestone_receipt(
    instance: Any,
    origin: models.Model | models.QuerySet[Any] | None = None,
    **kwargs: Any,
) -> None:
    """Apply the milestone owner's rule to instance, queryset and cascade deletion."""

    if isinstance(instance, Milestone):
        instance.validate_deletion(origin=origin)
