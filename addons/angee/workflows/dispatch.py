"""Frozen delivery choices for materialized historical workflow migrations only."""

from enum import StrEnum

from django.db import models


class WorkflowDispatchKind(models.TextChoices, StrEnum):
    """Historical delivery values preserved for serialized migration fields."""

    ADVANCE = "advance"
    EXECUTE = "execute"
    DECISION_EXPIRE = "decision_expire"
    DECISION_ESCALATE = "decision_escalate"
    ARTIFACT_DELIVERY = "artifact_delivery"
    CHILD_CANCEL = "child_cancel"
    RUN_CANCEL = "run_cancel"
    RUN_SETTLE = "run_settle"
