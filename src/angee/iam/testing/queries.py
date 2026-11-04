"""Statement classification for shipped IAM query-capture budgets."""

from django.db.backends.base.base import BaseDatabaseWrapper


def is_permission_audit_insert(sql: str, connection: BaseDatabaseWrapper) -> bool:
    """Identify one INSERT into REBAC's permission-audit table."""

    # The shared fixture plugin imports before django.setup(); the model
    # import stays inside the call.
    from rebac.models import PermissionAuditEvent

    table = connection.ops.quote_name(PermissionAuditEvent._meta.db_table)
    return sql.upper().startswith(f"INSERT INTO {table}".upper())
