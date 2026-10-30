"""Native relation fields with foundation-owned collection behavior."""

from __future__ import annotations

from collections.abc import Sequence

from django.contrib.contenttypes.fields import GenericRelation
from django.db import DEFAULT_DB_ALIAS, models


class RetainedGenericRelation(GenericRelation):
    """Query retained edges without collecting them on the target's deletion."""

    def bulk_related_objects(
        self, objs: Sequence[models.Model], using: str = DEFAULT_DB_ALIAS,
    ) -> models.QuerySet[models.Model]:
        return models.QuerySet(model=self.related_model, using=using).none()
