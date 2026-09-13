"""Concrete IAM models used by the bare source-addon test harness."""

from __future__ import annotations

from angee.iam.models import Group as AbstractGroup
from angee.iam.models import IAMKind as AbstractIAMKind
from angee.iam.models import User as AbstractUser


class IAMKind(AbstractIAMKind):
    """Table-less kind anchor for live user attributes in the bare harness."""

    class Meta(AbstractIAMKind.Meta):
        abstract = False
        app_label = "iam"
        managed = False
        rebac_resource_type = "iam/kind"


class Group(AbstractGroup):
    """Concrete IAM group used by tests without running the composer."""

    class Meta(AbstractGroup.Meta):
        """Django model options for the canonical test IAM group."""

        abstract = False
        app_label = "iam"
        db_table = "test_iam_group"
        rebac_resource_type = "auth/group"
        rebac_id_attr = "pk"
        rebac_subject_relation = "member"


class User(AbstractUser):
    """Concrete IAM user used by tests without running the composer."""

    class Meta(AbstractUser.Meta):
        """Django model options for the canonical test IAM user."""

        abstract = False
        app_label = "iam"
        db_table = "test_iam_user"
        rebac_resource_type = "auth/user"
