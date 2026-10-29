"""Small Django expression bridges for the supported database backends."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models
from django.db.models.functions import SHA256

if TYPE_CHECKING:
    from django.db.backends.base.base import BaseDatabaseWrapper
    from django.db.models.sql.compiler import SQLCompiler, _AsSqlType


class JSONArraySubquery(models.Subquery):
    """Collect ordered JSON objects selected as ``_item``; no rows yield ``[]``.

    Django owns correlation and JSON object construction. Its ArraySubquery has
    no SQLite compiler, so this seam uses each backend's native JSON aggregate.
    """

    output_field = models.JSONField()
    template = "(SELECT COALESCE(jsonb_agg(_item), '[]'::jsonb) FROM (%(subquery)s) items)"

    def as_sqlite(
        self, compiler: SQLCompiler, connection: BaseDatabaseWrapper, **extra_context: Any,
    ) -> _AsSqlType:
        return super().as_sql(
            compiler,
            connection,
            template="json((SELECT json_group_array(json(_item)) FROM (%(subquery)s) items))",
            **extra_context,
        )


class TextSHA256(SHA256):
    """Django's text digest using PostgreSQL's built-in SHA256, without pgcrypto."""

    def as_postgresql(
        self, compiler: SQLCompiler, connection: BaseDatabaseWrapper, **extra_context: Any,
    ) -> _AsSqlType:
        return super().as_sql(
            compiler,
            connection,
            template="encode(sha256(convert_to(%(expressions)s, 'UTF8')), 'hex')",
            **extra_context,
        )


class WindowSum(models.Window):
    """Sum grouped aggregates before pagination, using Django's window compiler.

    Django's aggregate Sum rejects an aggregate input; this window sums the
    completed groups instead. Callers must establish that their counts are
    additive before using the result as a distinct population total.
    """

    template = "SUM(%(expression)s) OVER (%(window)s)"
