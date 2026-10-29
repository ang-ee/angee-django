"""Proposal retention on instance, queryset, and cascade deletion paths."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete

from angee.base.signals import connect_for_models
from angee.proposals.models import Proposal, Round


def connect() -> None:
    """Bind the model-owned draft rules to every concrete round and proposal."""

    connect_for_models(
        pre_delete, refuse_delete, applies=lambda model: issubclass(model, (Round, Proposal)),
        dispatch_uid="angee.proposals.delete_blocker",
    )


def refuse_delete(sender: Any, instance: Round | Proposal, **kwargs: Any) -> None:
    """Reject retained work under the same round-first lock order as submission."""

    current = instance.lock_for_delete()
    if current is not None and (message := current.delete_blocker()):
        raise ValidationError(message)
