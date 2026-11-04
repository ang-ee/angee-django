"""Statement classification for shipped IAM query-capture budgets."""

from django.db.backends.base.base import BaseDatabaseWrapper
from rebac.models import PermissionAuditEvent


def is_permission_audit_insert(sql: str, connection: BaseDatabaseWrapper) -> bool:
    """Identify one INSERT into REBAC's permission-audit table."""

    table = connection.ops.quote_name(PermissionAuditEvent._meta.db_table)
    return sql.upper().startswith(f"INSERT INTO {table}".upper())
