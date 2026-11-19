"""Revision budgets exclude witnesses while retaining permission SQL."""

import pytest
from django.db import connection
from django.db.models import Exists
from django.test.utils import CaptureQueriesContext
from rebac.models.generation import SchemaGeneration

from angee.base.testing.queries import is_rebac_revision_read, statement_body, statement_startswith


@pytest.mark.parametrize("prefix", [
    "",
    'WITH RECURSIVE ids AS (SELECT "other"."id" FROM "other") ',
    'WITH ids AS (SELECT \'literal ) SELECT\'), nested AS (SELECT * FROM ids) ',
    '/* SELECT ) */ WITH "ids)SELECT" AS (SELECT \'it\'\'s (\' /* ) */) -- SELECT (\n',
])
@pytest.mark.parametrize("body", [
    'SELECT "rows"."id", (SELECT 1) AS probe FROM "rows"',
    'SELECT DISTINCT "rows"."id" FROM "rows"',
    'UPDATE "rows" SET "name" = \'SELECT\'',
    'DELETE FROM "rows"',
    'INSERT INTO "rows" ("name") VALUES (\'SELECT\')',
])
def test_statement_body_keeps_outer_projection_and_verb(prefix, body):
    assert statement_body(prefix + body) == body


def test_statement_body_keeps_transaction_control():
    assert statement_body("BEGIN") == "BEGIN"


COMPOUND = '(SELECT "rows"."id" FROM "rows" GROUP BY 1) UNION ALL (SELECT "other"."id" FROM "other")'


@pytest.mark.parametrize("prefix", [
    "",
    'WITH RECURSIVE ids AS (SELECT "other"."id" FROM "other") ',
    # A CTE whose own body is a parenthesised compound, then one with a column list.
    'WITH RECURSIVE "sets" AS ((SELECT 1) UNION (SELECT 2)), "sample" ("x") AS MATERIALIZED (SELECT 3 LIMIT 5) ',
    'WITH "sample" AS NOT MATERIALIZED (SELECT 3) /* ) (SELECT */ ',
    'DECLARE "_django_curs_1" NO SCROLL CURSOR WITH HOLD FOR ',
    'DECLARE "_django_curs_1" NO SCROLL CURSOR WITH HOLD FOR WITH ids AS (SELECT 1) ',
])
def test_a_parenthesised_compound_statement_is_classified_by_its_first_member(prefix):
    """PostgreSQL receives a Django ``union()`` with each member in parentheses. No data verb
    stands outside them, and the statement was returned whole and counted as no read."""

    sql = prefix + COMPOUND
    assert statement_body(sql) == COMPOUND[1:]
    assert statement_startswith(sql, "SELECT ")
    assert statement_startswith(sql, 'SELECT "rows".')
    assert not statement_startswith(sql, 'SELECT "other".')
    assert not statement_startswith(sql, ("INSERT", "UPDATE", "DELETE"))


def test_a_compound_statement_nested_in_parentheses_is_classified_by_its_first_member():
    sql = 'WITH ids AS (SELECT 1) ((SELECT "rows"."id" FROM "rows") UNION (SELECT 2)) UNION ALL (SELECT 3)'
    assert statement_startswith(sql, 'SELECT "rows".')


@pytest.mark.parametrize("sql", [
    'EXPLAIN (ANALYZE, FORMAT JSON) SELECT "rows"."id" FROM "rows"',
    'WITH "sample" ("x") AS (SELECT 3) SELECT "rows"."id" FROM "rows" WHERE "rows"."id" IN (SELECT 1)',
    'SELECT "rows"."id" FROM "rows" UNION ALL SELECT "other"."id" FROM "other"',
])
def test_parentheses_that_open_no_statement_leave_the_outer_verb(sql):
    assert statement_body(sql).startswith('SELECT "rows"."id" FROM "rows"')


@pytest.mark.parametrize("sql", ["VALUES (1), (2)", "(VALUES (1)) UNION ALL (VALUES (2))", 'SET search_path TO "x"'])
def test_statements_without_a_data_verb_stay_whole(sql):
    assert statement_body(sql) == sql


@pytest.mark.django_db
def test_a_django_combined_query_is_one_read_on_every_vendor():
    """SQLite sends the members bare and PostgreSQL in parentheses: both are one SELECT."""

    generation = SchemaGeneration.objects.all()
    with CaptureQueriesContext(connection) as queries:
        list(generation.values_list("pk").union(generation.values_list("pk"), all=True))
    [query] = queries
    assert statement_startswith(query["sql"], "SELECT ")
    assert connection.vendor != "postgresql" or query["sql"].startswith("(SELECT ")


def test_statement_body_handles_large_permission_plans():
    body = 'SELECT "rows"."id" FROM "rows"'
    cte = " UNION ALL ".join("SELECT 1" for _ in range(2500))
    assert statement_body(f"WITH ids AS ({cte}) {body}") == body


@pytest.mark.parametrize("cursor", ["{sql}", 'DECLARE "_django_curs_1" NO SCROLL CURSOR WITH HOLD FOR {sql}'])
@pytest.mark.parametrize("cte", ["", 'WITH RECURSIVE ids AS (SELECT "other"."id" FROM "other") '])
def test_statement_classification_keeps_application_and_revision_counts(cursor, cte):
    """Inner reads never become application reads or hide the outer witness."""

    revision = 'SELECT "rebac_schemageneration"."revision" FROM "rebac_schemageneration"'
    queries = [cursor.format(sql=cte + body) for body in [
        'SELECT "rows"."id" FROM "rows"',
        'SELECT DISTINCT "rows"."id" FROM "rows"',
        revision,
        'SELECT 1 FROM "rebac_schemageneration"',
    ]]
    queries.append(f'WITH ids AS ({revision}) UPDATE "rows" SET "name" = \'SELECT\'')
    assert sum(statement_startswith(sql, "SELECT ") for sql in queries) == 4
    assert sum(statement_startswith(sql, 'SELECT "rows".') for sql in queries) == 1
    assert sum(statement_startswith(sql, "SELECT DISTINCT ") for sql in queries) == 1
    assert sum(is_rebac_revision_read(sql) for sql in queries) == 1
    assert sum(statement_startswith(sql, ("INSERT", "UPDATE", "DELETE")) for sql in queries) == 1
    assert not any(statement_startswith(sql, 'SELECT "other".') for sql in queries)


@pytest.mark.django_db
def test_revision_classifier_keeps_permission_probes_and_fenced_row_reads_in_the_budget():
    """The schema-generation FROM clause alone does not identify a witness."""

    generation = SchemaGeneration.objects.all()
    with CaptureQueriesContext(connection) as queries:
        list(generation.values_list("revision", flat=True))
        generation.exists()
        list(generation.annotate(_fence=Exists(generation)).values_list("pk", "_fence"))

    assert len(queries) == 3
    assert [is_rebac_revision_read(query["sql"]) for query in queries] == [True, False, False]
