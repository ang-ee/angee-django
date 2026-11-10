"""Classify native Django query captures by their outer statement.

CTE definitions are part of one statement, not extra application reads. Use the
sqlparse lexer rather than grouping the full syntax tree: permission plans can
exceed the parser's grouping limits. This module imports before Django setup.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from django.db import NotSupportedError, connection
from sqlparse.lexer import tokenize
from sqlparse.tokens import DML, Punctuation


def statement_body(sql: str) -> str:
    """Return the data statement after CTEs or a PostgreSQL cursor declaration.

    Parentheses inside comments, literals and quoted identifiers do not alter
    nesting. Preserve the outer projection and predicates verbatim so callers
    can still assert their shape. Statements without a data verb are unchanged.
    """

    tokens = tokenize(sql)
    depth = 0
    for kind, value in tokens:
        if depth == 0 and kind is DML:
            return value + "".join(value for _, value in tokens)
        if kind is Punctuation:
            depth += (value == "(") - (value == ")")
    return sql


def statement_startswith(sql: str, prefix: str | tuple[str, ...]) -> bool:
    """Match an exact outer-statement prefix, excluding cursor wrappers and CTEs.

    A table/column prefix distinguishes application projections from permission
    reads; a verb prefix counts all reads or writes. Matching is case-sensitive,
    like the SQL Django emits. Each captured entry still counts as one statement.
    """

    return statement_body(sql).startswith(prefix)


def is_rebac_revision_read(sql: str) -> bool:
    """Identify revision witnesses without excluding fenced permission reads."""

    return statement_startswith(sql, 'SELECT "rebac_schemageneration"."revision"')


@dataclass(frozen=True)
class PlannerMemory:
    """PostgreSQL's planning allocation for one captured, parameterized statement, in KiB."""

    sql_bytes: int
    used_kib: int
    allocated_kib: int


@contextmanager
def capture_planner_memory(match: Callable[[str], bool]) -> Iterator[list[PlannerMemory]]:
    """EXPLAIN matching read statements after the read; populate measurements on context exit.

    Capture SQL and parameters together through Django's execute wrapper. EXPLAIN
    never executes the read again (no ANALYZE). A document may plan multiple
    statements: summing their allocations measures total planning work, not peak
    resident memory. Callers assert the expected statement count and returned rows.
    """

    if connection.vendor != "postgresql" or connection.get_database_version() < (17,):
        raise NotSupportedError("EXPLAIN MEMORY requires PostgreSQL 17 or later")
    statements = []
    plans: list[PlannerMemory] = []

    def capture(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        if match(sql):
            statements.append((sql, params))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        yield plans
    with connection.cursor() as cursor:
        for sql, params in statements:
            cursor.execute("EXPLAIN (MEMORY, SUMMARY, FORMAT JSON) " + sql, params)
            planning = cursor.fetchone()[0][0]["Planning"]
            plans.append(PlannerMemory(len(sql.encode()), planning["Memory Used"], planning["Memory Allocated"]))
