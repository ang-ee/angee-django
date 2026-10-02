"""Revision budgets exclude witnesses while retaining permission SQL."""

import pytest
from django.db import connection
from django.db.models import Exists
from django.test.utils import CaptureQueriesContext
from rebac.models.generation import SchemaGeneration

from tests.queries import is_rebac_revision_read


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
