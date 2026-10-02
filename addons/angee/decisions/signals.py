"""Lifecycle notifications and evidence deletion protection."""

from typing import Any

from django.apps import apps
from django.db.models.signals import post_delete
from django.dispatch import Signal

decision_group_settled = Signal()
"""Sent after commit with ``sender=DecisionGroup``, ``group`` and its ``outcome``."""


def connect() -> None:
    """Protect referenced evidence on instance and collection deletion."""
    post_delete.connect(protect_evidence, dispatch_uid="decisions.protect_evidence")


def protect_evidence(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Delegate deletion policy to the evidence owner."""
    if sender._meta.apps is not apps:
        return
    try:
        if apps.get_model(sender._meta.label) is not sender:
            return
        evidence_model = apps.get_model("decisions", "DecisionEvidence")
    except LookupError:
        return
    evidence_model.objects.protect_record(instance)
