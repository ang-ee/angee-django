"""Statement classification for native Django query-capture budgets."""

from sqlparse.lexer import tokenize
from sqlparse.tokens import DML, Punctuation


def statement_body(sql: str) -> str:
    """Find the outer verb in SQL tokens without grouping large permission plans."""

    tokens = tokenize(sql)
    depth = 0
    for kind, value in tokens:
        if depth == 0 and kind is DML:
            return value + "".join(value for _, value in tokens)
        if kind is Punctuation:
            depth += (value == "(") - (value == ")")
    return sql


def is_rebac_revision_read(sql: str) -> bool:
    """Identify revision witnesses without excluding fenced permission reads."""

    return sql.startswith('SELECT "rebac_schemageneration"."revision"')
