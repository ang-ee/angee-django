"""Lifecycle notifications and evidence deletion protection."""

from typing import Any

from django.apps import apps
from django.db.models.signals import post_delete
from django.dispatch import Signal

from angee.base.refs import is_record_target_model
from angee.base.signals import connect_for_models

decision_group_settled = Signal()
"""Sent after commit with ``sender=DecisionGroup``, ``group`` and its ``outcome``."""


def connect() -> None:
    """Protect referenced evidence on instance and collection deletion of evidence-capable records."""
    connect_for_models(post_delete, protect_evidence, applies=is_record_target_model,
                       dispatch_uid="decisions.protect_evidence")


def protect_evidence(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Delegate deletion policy to the evidence owner."""
    apps.get_model("decisions", "DecisionEvidence").objects.protect_record(instance)
