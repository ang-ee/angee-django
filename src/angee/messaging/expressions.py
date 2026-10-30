"""Lazy projections of messaging-owned body-part lineage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models

if TYPE_CHECKING:
    from django.db.backends.base.base import BaseDatabaseWrapper
    from django.db.models.sql.compiler import SQLCompiler, _AsSqlType


class AttachedFileSkipReason(models.Func):
    """Classify one file use against actor-readable ancestors of its message.

    UNION makes the recursive walk cycle-safe. Both the starting row and each
    ancestor come from the supplied readable Part queryset; foreign parents
    never enter the lineage. An empty reason identifies an attached file.
    """

    arity = 2

    def __init__(self, readable: models.QuerySet[Any]) -> None:
        columns = ("pk", "parent_id", "message_id", "type", "role", "cid", "name",
                   "file_id", "disposition", "file__size_bytes")
        super().__init__(
            models.F("pk"),
            models.Subquery(
                readable.filter(message_id=models.OuterRef("message_id")).order_by().values(*columns),
                output_field=models.IntegerField(),
            ),
            output_field=models.CharField(),
        )

    def as_sql(
        self, compiler: SQLCompiler, connection: BaseDatabaseWrapper, **extra_context: Any,
    ) -> _AsSqlType:
        identity_expression, readable_expression = self.get_source_expressions()
        identity, identity_params = compiler.compile(identity_expression)
        readable, readable_params = compiler.compile(readable_expression)
        # CTE column names deliberately belong to this expression rather than
        # depending on aliases selected by Django's scoped queryset compiler.
        mime = ("LOWER(TRIM(SUBSTR(type, 1, INSTR(type || ';', ';') - 1)))"
                if connection.vendor == "sqlite" else "LOWER(TRIM(SPLIT_PART(type, ';', 1)))")
        sql = f"""(WITH RECURSIVE readable
            (id, parent_id, message_id, type, role, cid, name, file_id, disposition, size_bytes)
            AS {readable}, lineage AS (
                SELECT * FROM readable WHERE id = {identity}
                UNION
                SELECT p.* FROM readable p JOIN lineage child
                    ON p.id = child.parent_id AND p.message_id = child.message_id
            ), typed AS (SELECT *, {mime} AS mime FROM lineage)
            SELECT CASE
                WHEN EXISTS (SELECT 1 FROM typed WHERE role = 'signature' OR mime IN
                    ('application/pgp-signature', 'application/pkcs7-signature',
                     'application/x-pkcs7-signature')) THEN 'signature'
                WHEN EXISTS (SELECT 1 FROM typed WHERE mime IN
                    ('multipart/report', 'message/delivery-status', 'message/global-delivery-status',
                     'message/disposition-notification', 'message/global-disposition-notification'))
                    THEN 'delivery_report'
                WHEN EXISTS (SELECT 1 FROM typed WHERE cid <> '' OR
                    (file_id IS NOT NULL AND disposition = 'inline' AND
                        (name = '' OR NOT (mime IN ('application/pdf', 'application/xml', 'text/xml')
                            OR mime LIKE '%%+xml')))) THEN 'inline'
                WHEN (SELECT COUNT(*) FROM typed WHERE id <> {identity}
                    AND mime = 'message/rfc822') > 1 THEN 'forward_depth'
                WHEN EXISTS (SELECT 1 FROM typed WHERE id = {identity}
                    AND mime = 'message/rfc822') THEN 'forwarded_message'
                WHEN EXISTS (SELECT 1 FROM typed WHERE id = {identity}
                    AND (size_bytes IS NULL OR size_bytes = 0)) THEN 'empty_file'
                ELSE '' END)"""
        return sql, [*readable_params, *identity_params, *identity_params, *identity_params, *identity_params]
