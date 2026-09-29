"""Queryable facts derived from retained workflow execution links."""

from typing import Any

from django.db import models

from angee.graphql.field_types import register_field_type
from angee.workflows.states import RunOrigin


class RunOriginField(models.GeneratedField):
    """Derive origin in the database so filtering and grouping share one rule."""

    choices_enum = RunOrigin

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("expression", models.Case(
            models.When(reprocess_of__isnull=False, then=models.Value(RunOrigin.REPROCESS)),
            default=models.Value(RunOrigin.MANUAL),
        ))
        kwargs.setdefault("output_field", models.CharField(max_length=max(map(len, RunOrigin.values))))
        kwargs.setdefault("choices", RunOrigin.choices)
        kwargs.setdefault("db_persist", True)
        super().__init__(**kwargs)


register_field_type(RunOriginField, str)
