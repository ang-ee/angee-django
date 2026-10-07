"""`row_annotations`: an optimized row keeps its annotation; a payload row reads it once."""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.models import Value
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.graphql.data import row_annotations
from angee.tags.testing.models import Tag

pytestmark = pytest.mark.django_db


def test_an_annotated_row_keeps_its_values_and_a_payload_row_reads_the_missing_ones_at_once() -> None:
    with system_context(reason="test.row_annotations.seed"):
        tag = Tag.objects.create(name="Urgent", color="")
    annotations = {"_seen": Value(1), "_also": Value(2)}

    # What the optimizer annotated on a read is used as is, with no query.
    tag._seen, tag._also = 5, 6
    with CaptureQueriesContext(connection) as queries:
        assert row_annotations(tag, annotations, Tag.system_queryset()) == {"_seen": 5, "_also": 6}
    assert not queries

    # A mutation payload carries no annotations: the missing ones are read for that row.
    del tag._also
    assert row_annotations(tag, annotations, Tag.system_queryset()) == {"_seen": 5, "_also": 2}
    payload = Tag.system_queryset().get(pk=tag.pk)
    assert row_annotations(payload, annotations, Tag.system_queryset()) == {"_seen": 1, "_also": 2}
