"""Protected source declaration for a neutral retained derivation edge."""

from angee.base.models.derived import DerivedFrom
from angee.iam.models.append_only import ProtectedAppendOnlyModel
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.refs import ProtectedRecordRefMixin


class ProtectedDerivedFrom(
    DerivedFrom, ProtectedAppendOnlyModel, ProtectedRecordRefMixin, ProtectedDataModel, concrete=False
):
    """Compose native IAM declaration/manager contracts without duplicating edge fields."""

    class Meta:
        abstract = True
