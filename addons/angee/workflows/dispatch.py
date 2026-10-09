"""Dispatch constants that released workflow migrations import by path.

The workflows engine was rebuilt (``752281db0``) and no longer has a durable
dispatcher. Migration history materialized before the rebuild names these
kinds in its constraints, so this module keeps that import path loadable. Do
not use it in new code.
"""

from enum import StrEnum

from django.db import models


class WorkflowDispatchKind(models.TextChoices, StrEnum):
    """Released history only: the retired dispatcher's delivery kinds."""

    ADVANCE = "advance"
    EXECUTE = "execute"
    DECISION_EXPIRE = "decision_expire"
    DECISION_ESCALATE = "decision_escalate"
    ARTIFACT_DELIVERY = "artifact_delivery"
    CHILD_CANCEL = "child_cancel"
    RUN_CANCEL = "run_cancel"
    RUN_SETTLE = "run_settle"
