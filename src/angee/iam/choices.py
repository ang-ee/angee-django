"""Identity kinds and account verbs owned by IAM."""

from django.db import models


class UserKind(models.TextChoices):
    """Kind of IAM principal stored in the swappable user table."""

    PERSON = "person", "Person"
    SERVICE = "service", "Service"


class UserAccountAction(models.TextChoices):
    """Account verbs run on another person's account, each named by its Zed permission."""

    SET_ACTIVE = "set_active", "Set active"
    RENAME = "rename", "Rename"
    RESET_PASSWORD = "reset_password", "Reset password"
    ISSUE_PASSWORD = "issue_password", "Issue password"
