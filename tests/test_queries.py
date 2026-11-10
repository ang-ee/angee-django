"""Revision budgets exclude witnesses while retaining permission SQL."""

import pytest
from django.db import connection
from django.db.models import Exists
from django.test.utils import CaptureQueriesContext
from rebac.models.generation import SchemaGeneration

from tests.queries import is_rebac_revision_read, statement_body


@pytest.mark.parametrize("prefix", [
    "",
    'WITH RECURSIVE ids AS (SELECT "other"."id" FROM "other") ',
    'WITH ids AS (SELECT \'literal ) SELECT\'), nested AS (SELECT * FROM ids) ',
])
@pytest.mark.parametrize("body", [
    'SELECT "rows"."id", (SELECT 1) AS probe FROM "rows"',
    'SELECT DISTINCT "rows"."id" FROM "rows"',
    'UPDATE "rows" SET "name" = \'SELECT\'',
    'DELETE FROM "rows"',
])
def test_statement_body_keeps_outer_projection_and_verb(prefix, body):
    assert statement_body(prefix + body) == body


def test_statement_body_keeps_transaction_control():
    assert statement_body("BEGIN") == "BEGIN"


def test_statement_body_handles_large_permission_plans():
    body = 'SELECT "rows"."id" FROM "rows"'
    cte = " UNION ALL ".join("SELECT 1" for _ in range(2500))
    assert statement_body(f"WITH ids AS ({cte}) {body}") == body


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
