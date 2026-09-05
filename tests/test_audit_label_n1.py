"""Native prefetch query budgets for scalar-only audit user labels."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.models import PermissionAuditEvent

from angee.iam.identity import user_display_labels
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

    IAM's label queryset projects the user's
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


@pytest.mark.parametrize(
    ("selection", "label_queries"),
    [
        ("created_by_label", 1),
        ("created_by_label updated_by_label", 2),
        ("created_by updated_by", 0),
    ],
)
def test_audited_list_labels_batch_distinct_authors(knowledge_tables: None, selection: str, label_queries: int) -> None:
    """One or 25 distinct authors cost one query per selected label relation."""

    alice = create_user("alice")
    vault = vault_for(alice, name="Research")
    authors = [create_user(f"author-{index}") for index in range(25)]
    _author_pages(vault, authors, len(authors))
    schema = addon_schema(knowledge_schema.schemas, "public")
    query = "query Labels($limit: Int!) { pages(limit: $limit, order_by: [{title: asc}]) { id " + selection + " } }"
    query_counts = []
    actor = to_subject_ref(alice)
    audits = PermissionAuditEvent.objects.filter(
        kind=PermissionAuditEvent.KIND_SUDO_BYPASS,
        reason="iam.identity.user_label",
        actor_subject_type=actor.subject_type,
        actor_subject_id=actor.subject_id,
    )
    before_audits = audits.count()
    for size in (1, 25):
        with CaptureQueriesContext(connection) as captured:
            rows = result_data(execute_schema(schema, query, {"limit": size}, user=alice))["pages"]
        assert len(rows) == size
        if "created_by_label" in selection:
            assert {row["created_by_label"] for row in rows} <= {author.username for author in authors}
        if "updated_by_label" in selection:
            assert all(row["updated_by_label"] == row["created_by_label"] for row in rows)
        query_counts.append(len(_label_reads(captured)))
        assert not any(
            get_user_model()._meta.db_table in item["sql"] and "password" in item["sql"] for item in captured
        ), "Scalar label loading must not fetch credentials"

    assert query_counts == [label_queries, label_queries]
    assert audits.count() - before_audits == 2 * label_queries

    # Scalar labels intentionally remain available where guarded User rows do not.
    with actor_context(alice):
        assert not get_user_model().objects.filter(pk__in=[author.pk for author in authors]).exists()


def test_audited_label_prefetch_preserves_missing_authors(knowledge_tables: None) -> None:
    """Nullable audit references resolve without spurious User reads."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="No attribution")
    with system_context(reason="test nullable audit references"):
        # Exercise the nullable state retained by Django's SET_NULL without
        # requiring unrelated optional test models' schema in this fixture.
        Page.objects.filter(pk=page.pk).update(created_by=None, updated_by=None)
    schema = addon_schema(knowledge_schema.schemas, "public")
    with CaptureQueriesContext(connection) as captured:
        rows = result_data(execute_schema(schema, _LABELS_QUERY, user=alice))["pages"]
    assert len(rows) == 1
    assert rows[0]["created_by_label"] is None
    assert rows[0]["updated_by_label"] is None
    assert _label_reads(captured) == []


def test_vault_owner_labels_batch_distinct_owners(knowledge_tables: None) -> None:
    """Visible vaults expose owner labels with one query, without exposing users."""

    reader = create_user("vault-reader")
    owners = [create_user(f"vault-owner-{index}") for index in range(25)]
    vaults = [vault_for(owner) for owner in owners]
    write_relationships(
        [
            RelationshipTuple(resource=to_object_ref(vault), relation="viewer", subject=to_subject_ref(reader))
            for vault in vaults
        ]
    )
    schema = addon_schema(knowledge_schema.schemas, "public")
    query = "query VaultOwners($limit: Int!) { vaults(limit: $limit) { id owner_label } }"
    counts = []
    for size in (1, 25):
        with CaptureQueriesContext(connection) as captured:
            rows = result_data(execute_schema(schema, query, {"limit": size}, user=reader))["vaults"]
        assert len(rows) == size
        assert {row["owner_label"] for row in rows} <= {owner.username for owner in owners}
        counts.append(len(_label_reads(captured)))
    assert counts == [1, 1]
    with actor_context(reader):
        assert not get_user_model().objects.filter(pk__in=[owner.pk for owner in owners]).exists()


def test_user_display_labels_batches_and_primes_request_memo(transactional_db: Any) -> None:
    """The batch label owner resolves many user ids with one elevated read."""

    del transactional_db
    alice = create_user("batch-alice")
    bob = create_user("batch-bob")
    request = RequestFactory().get("/")

    with CaptureQueriesContext(connection) as first:
        labels = user_display_labels([alice.pk, bob.pk, alice.pk], request=request)

    assert labels == {
        alice.pk: "batch-alice",
        bob.pk: "batch-bob",
    }
    assert len(_label_reads(first)) == 1

    with CaptureQueriesContext(connection) as cached:
        assert user_display_labels([alice.pk, bob.pk], request=request) == labels
    assert _label_reads(cached) == []
