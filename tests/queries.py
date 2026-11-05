"""Statement classification for native Django query-capture budgets."""


def is_rebac_revision_read(sql: str) -> bool:
    """Identify revision witnesses without excluding fenced permission reads."""

    return sql.startswith('SELECT "rebac_schemageneration"."revision"')
