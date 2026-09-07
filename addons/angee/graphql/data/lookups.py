"""Django-native backend-specific Hasura text operators.

The Hasura comparison/evaluator owners expose the configurable lookup seam.
PostgreSQL's SIMILAR TO is kept as SQL with bound operands, never translated to
another pattern language. Computed resources retain the portable vocabulary.
"""

from __future__ import annotations

from typing import Any

from django.db import connection, models
from django.db.utils import NotSupportedError

from angee.graphql.introspection import FieldPathError, require_field_for_path


class SimilarLookup(models.Lookup):
    """PostgreSQL SQL-pattern lookup, available only on opted-in model fields."""

    lookup_name = "angee_similar"

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, list[Any]]:
        if connection.vendor != "postgresql":
            raise NotSupportedError("SIMILAR TO requires PostgreSQL.")
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{lhs} SIMILAR TO {rhs}", [*lhs_params, *rhs_params]


def resource_filter_lookups(model: type[models.Model], fields: tuple[str, ...]) -> dict[str, tuple[str, bool]]:
    """Bind native field lookups supported by this resource's database backend."""

    lookups = {"iregex": ("__iregex", False)}
    if connection.vendor == "postgresql":
        for path in fields:
            if path == "id":
                continue  # Hasura's public identity column is not a text axis.
            try:
                field = require_field_for_path(model, path)
            except FieldPathError as error:
                if error.to_many:
                    # Native Hasura supports relation membership filters. Their
                    # ID operands need no PostgreSQL text lookup registration.
                    continue
                raise
            if isinstance(field, (models.CharField, models.TextField)):
                field.register_lookup(SimilarLookup)
        lookups.update(similar=("__angee_similar", False), nsimilar=("__angee_similar", True))
    return lookups
