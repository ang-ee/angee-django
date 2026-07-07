"""Concrete IAM models used by the bare source-addon test harness."""

from __future__ import annotations

from django.db import models

from angee.base.models import AngeeDataModel
from angee.iam.models import Company as AbstractCompany
from angee.iam.models import CompanyScopedMixin
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


class Company(AbstractCompany):
    """Concrete IAM company of record used by tests without the composer."""

    class Meta(AbstractCompany.Meta):
        """Django model options for the canonical test IAM company."""

        abstract = False
        app_label = "iam"
        db_table = "test_iam_company"
        rebac_resource_type = "iam/company"
        rebac_id_attr = "sqid"


class CompanyScopedDoc(CompanyScopedMixin, AngeeDataModel):
    """Concrete company-scoped row exercising the default-company-on-insert mixin.

    Carries no ``rebac_resource_type`` of its own (like other row-policy-free
    concrete test rows): the behavior under test is :class:`CompanyScopedMixin`
    defaulting the ``company`` FK from the acting user's membership, not a scoped
    read.
    """

    sqid_prefix = "csd_"

    title = models.CharField(max_length=200, blank=True, default="")

    class Meta(AngeeDataModel.Meta):
        """Django model options for the canonical company-scoped test row."""

        abstract = False
        app_label = "iam"
        db_table = "test_iam_company_scoped_doc"
        rebac_id_attr = "sqid"
