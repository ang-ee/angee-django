"""Concrete IAM models used by the bare source-addon test harness."""

from __future__ import annotations

from angee.iam.models import User as AbstractUser


class User(AbstractUser):
    """Concrete IAM user used by tests without running the composer."""

    class Meta(AbstractUser.Meta):
        """Django model options for the canonical test IAM user."""

        abstract = False
        app_label = "iam"
        db_table = "test_iam_user"
        rebac_resource_type = "auth/user"
        rebac_id_attr = "sqid"
