"""Derived fields owned by the record synchronization protocol."""

from typing import Any

from django.db import models

from angee.graphql.field_types import register_field_type
from angee.integrate.impl import DiscrepancyStatus


class DiscrepancyOpenField(models.GeneratedField):
    """A filterable boolean derived by Django from unresolved quarantine status.

    Native generated storage keeps bulk updates and ordinary saves consistent;
    the field's scalar registration also serves Hasura comparison inputs.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("expression", models.Q(status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY)))
        kwargs.setdefault("output_field", models.BooleanField())
        kwargs.setdefault("db_persist", True)
        super().__init__(**kwargs)


register_field_type(DiscrepancyOpenField, bool)
