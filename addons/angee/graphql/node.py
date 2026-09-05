"""GraphQL identity primitive for Angee types."""

from __future__ import annotations

from typing import cast

import strawberry
from django.db import models

from angee.base.identity import public_id_of
from angee.graphql.ids import PublicID

NODE_DISPLAY_NAME_DESCRIPTION = "Human-readable label for this object."


@strawberry.interface(name="Node")
class AngeeNode:
    """GraphQL object whose id field is the model's public id."""

    @strawberry.field(description="The public ID of this object.")
    def id(self) -> PublicID:
        """Return this row's public id."""

        return PublicID(public_id_of(cast(models.Model, self)))

    @strawberry.field(description=NODE_DISPLAY_NAME_DESCRIPTION)
    def display_name(self) -> str:
        """Return the record's human label — the uniform alias of ``str(self)``.

        Every node carries a label without each type re-declaring one. A model
        that stores an editable, searchable label declares its own
        ``display_name`` field, which overrides this resolver on that type.
        A computed label declares its model dependencies on the concrete type
        with native ``strawberry_django.field(resolver=AngeeNode.display_name,
        only=[...])`` hints, so narrow selections do not defer label fields.
        """

        return str(cast(models.Model, self))
