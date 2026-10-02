"""Native Django deletion dispatch for retained milestone receipts."""

from typing import Any

from django.db import models
from django.db.models.signals import pre_delete

from angee.base.signals import connect_for_models
from angee.projects.models import Milestone


def connect() -> None:
    """Bind the receipt rule to the milestone models only."""
    connect_for_models(pre_delete, retain_milestone_receipt,
                       applies=lambda model: issubclass(model, Milestone),
                       dispatch_uid="projects.milestone.retain_receipt")


def retain_milestone_receipt(
    instance: Any,
    origin: models.Model | models.QuerySet[Any] | None = None,
    **kwargs: Any,
) -> None:
    """Apply the milestone owner's rule to instance, queryset and cascade deletion."""

    instance.validate_deletion(origin=origin)
