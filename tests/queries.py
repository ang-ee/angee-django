"""Statement classification for native Django query-capture budgets."""

from angee.iam.testing.queries import is_permission_audit_insert as is_permission_audit_insert


def is_rebac_revision_read(sql: str) -> bool:
    """Identify revision witnesses without excluding fenced permission reads."""

    return sql.startswith('SELECT "rebac_schemageneration"."revision"')
