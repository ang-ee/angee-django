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
