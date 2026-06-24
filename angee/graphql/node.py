"""GraphQL identity primitive for Angee types."""

from __future__ import annotations

from typing import cast

import strawberry
from django.db import models

from angee.base.models import public_id_of
from angee.graphql.ids import PublicID


@strawberry.interface(name="Node")
class AngeeNode:
    """GraphQL object whose id field is the model's public id."""

    @strawberry.field(description="The public ID of this object.")
    def id(self) -> PublicID:
        """Return this row's public id."""

        return PublicID(public_id_of(cast(models.Model, self)))
