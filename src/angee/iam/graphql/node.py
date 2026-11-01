"""IAM-owned redaction on a native Strawberry node interface."""

from typing import cast
import strawberry
from angee.graphql.node import AngeeNode
from angee.iam.models.base import ProtectedModel


@strawberry.interface
class ProtectedNode(AngeeNode):
    @strawberry.field(description="Field names hidden from this actor by the record's read policy.")
    def redacted_fields(self) -> list[str]:
        return sorted(cast(ProtectedModel, self).redacted_fields())
