"""Query-count proof for the audited-list user-label N+1 fix.

``AuthoredRefMixin.created_by_label`` / ``updated_by_label`` (``angee.iam.audit``)
resolve through ``user_display_label``, which runs its own
``User.objects.filter(pk=...)`` read under ``system_context`` — a custom resolver
the strawberry-django optimizer cannot batch. Without the request-scoped memo this
is a strict per-row N+1 on every audited list. The knowledge ``pages`` list is the
real production path (``PageType(AuthoredRefMixin, AngeeNode)`` over an
``AuditMixin`` model), so this proves the memo collapses the User reads to one per
*distinct* author (K), independent of the page count (N).
"""

from __future__ import annotations

import importlib
from typing import Any

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.iam.identity import user_display_label
from tests.conftest import (
    Page,
    addon_schema,
    create_user,
    execute_schema,
    result_data,
    vault_for,
)

knowledge_schema = importlib.import_module("angee.knowledge.schema")

_LABELS_QUERY = "query { pages { id created_by_label updated_by_label } }"


def _label_reads(captured: CaptureQueriesContext) -> list[str]:
    """Return the captured SQL that are user-label reads.

    ``user_display_label`` is the only reader that projects the user's
    ``first_name``/``last_name`` through ``.only(...)`` — a narrow projection a
    full ``SELECT *`` user load (which also carries ``password``) never matches —
    so this isolates its reads from the list/actor-scope queries.
    """

    table = get_user_model()._meta.db_table
    return [
        query["sql"]
        for query in captured.captured_queries
        if table in query["sql"]
        and "first_name" in query["sql"]
        and "last_name" in query["sql"]
        and "password" not in query["sql"]
    ]


def _author_pages(vault: Any, authors: list[Any], count: int, *, start: int = 0) -> None:
    """Create ``count`` pages in ``vault`` re-stamped round-robin to ``authors``.

    Titles run from ``start`` so successive batches never collide on the model's
    unique ``(vault, title)``. Pages are created by the vault owner (so they stay
    actor-readable), then ``created_by``/``updated_by`` are re-pointed to the K
    author users — the FK targets the label resolver reads under elevation,
    distinct from row scope.
    """

    with actor_context(vault.owner):
        pages = [Page.objects.create_in(vault, title=f"Page {start + index}") for index in range(count)]
    with system_context(reason="test author re-stamp"):
        for index, page in enumerate(pages):
            author = authors[index % len(authors)]
            Page.objects.filter(pk=page.pk).update(created_by=author, updated_by=author)


def test_audited_list_labels_scale_with_distinct_authors_not_rows(knowledge_tables: None) -> None:
    """The audited list's User reads track K distinct authors, not N rows."""

    row_count = 8
    author_count = 2
    alice = create_user("alice")
    vault = vault_for(alice, name="Research")
    authors = [create_user(f"author-{index}") for index in range(author_count)]
    _author_pages(vault, authors, row_count)

    schema = addon_schema(knowledge_schema.schemas, "public")

    # AFTER — production path, request-scoped memo: one User read per distinct author.
    with CaptureQueriesContext(connection) as after:
        rows = result_data(execute_schema(schema, _LABELS_QUERY, user=alice))["pages"]
    assert len(rows) == row_count
    after_reads = _label_reads(after)
    assert len(after_reads) == author_count, after_reads
    assert len(after_reads) < row_count

    # BEFORE — pre-fix per-row path (no memo): one User read per label call → 2N.
    with CaptureQueriesContext(connection) as before:
        for index in range(row_count):
            author_pk = authors[index % author_count].pk
            user_display_label(author_pk)  # created_by_label
            user_display_label(author_pk)  # updated_by_label
    assert len(_label_reads(before)) == 2 * row_count

    # FLAT — doubling the page count with the same authors keeps the read count at K.
    _author_pages(vault, authors, row_count, start=row_count)
    with CaptureQueriesContext(connection) as doubled:
        doubled_rows = result_data(execute_schema(schema, _LABELS_QUERY, user=alice))["pages"]
    assert len(doubled_rows) == 2 * row_count
    assert len(_label_reads(doubled)) == author_count
