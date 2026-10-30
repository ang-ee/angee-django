"""Intake's model-independent choices, shared by the models and their import adapter."""

from django.db import models


class NeedAccessAction(models.TextChoices):
    """Authored transitions for request access."""

    INTAKE_APPROVE = "intake.approve", "Approve"
    INTAKE_DENY = "intake.deny", "Deny"
