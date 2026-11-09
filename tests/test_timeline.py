"""Record timeline reads exercise committed owner writes and actor-scoped SQL."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import actor_context

from angee.base.models.identity import public_id_of
from angee.base.pagination import KeysetOrder, keyset_signer
from angee.base.signals import mute_changes
from angee.graphql.testing.documents import GraphQLDocumentRead, execute_schema, measure_graphql_document, result_data
from angee.messaging.timeline import CommentKind
from angee.provenance.context import changeset as open_changeset
from angee.timeline import schema as timeline_schema
from angee.timeline.kinds import timeline_kinds
from angee.timeline.reading import Timeline
from tests.conftest import addon_schema, create_platform_admin, create_user, vault_for

KINDS = [
    "angee.provenance.timeline.timeline_kinds",
    "angee.decisions.timeline.timeline_kinds",
    "angee.workflows.timeline.timeline_kinds",
    "angee.messaging.timeline.timeline_kinds",
    "angee.projects.timeline.timeline_kinds",
]

QUERY = """
query($model: String!, $id: ID!, $kinds: [String!], $needsMe: Boolean, $before: String, $limit: Int) {
  timeline(model: $model, id: $id, kinds: $kinds, needs_me: $needsMe, before: $before, limit: $limit) {
    kinds { id count } needs_me has_more next_before
    entries { key at kind title actor detail needs_me target_model target_id children { kind count } }
  }
  timeline_attention(model: $model, id: $id)
}
"""


def read_timeline(record, actor, **variables):
    """Exercise both public reads under the supplied nonambient actor."""
    schema = addon_schema(timeline_schema.schemas, "console")
    return result_data(execute_schema(
        schema, QUERY, {"model": record._meta.label, "id": public_id_of(record), **variables}, user=actor,
    ))


def test_cursor_round_trips_and_orders_the_page_totally() -> None:
    at = datetime(2026, 10, 19, 9, 41, tzinfo=UTC)
    order = KeysetOrder("at", tie_breakers=("kind",), key_field="pk")
    signer = keyset_signer(["record", "all-kinds"], salt="angee.timeline")
    token = order.sign_position((at, "provenance.change", 7), signer)
    cursor = order.unsign(token, signer, models.BigIntegerField())
    assert cursor == (at, "provenance.change", 7)
    predicate = str(order.before(cursor, prefix="_timeline_"))
    assert predicate.count("_timeline_at__lt") == 1 and predicate.count("_timeline_kind__gt") == 1
    assert predicate.count("_timeline_pk__lt") == 1 and "'pk__" not in predicate
    # One declaration orders the page and the union's renamed result columns alike.
    assert order.ordering(prefix="_timeline_") == ("-_timeline_at", "_timeline_kind", "-_timeline_pk")
    assert order.ordering(prefix="_result_") == ("-_result_at", "_result_kind", "-_result_pk")
    assert KeysetOrder("created_at").ordering() == ("-created_at", "-pk")
    with pytest.raises(ValueError):
        order.unsign("nonsense", signer, models.BigIntegerField())
    with pytest.raises(ValueError):
        order.unsign(token + "tampered", signer, models.BigIntegerField())
    with pytest.raises(ValueError):
        order.unsign(
            token, keyset_signer(["other-record", "all-kinds"], salt="angee.timeline"), models.BigIntegerField()
        )


@override_settings(ANGEE_TIMELINE_KINDS=KINDS)
def test_kinds_resolve_once_each_in_sequence_order() -> None:
    assert [kind.id for kind in timeline_kinds()] == [
        "workflows.run", "decisions.decision", "provenance.change", "messaging.comment", "projects.task",
        "messaging.conversation",
    ]


def test_observed_fields_include_foreign_key_storage_names() -> None:
    kind = CommentKind()
    message = apps.get_model("messaging", "Message")
    assert kind.relevant(message, {"thread"})
    assert kind.relevant(message, {"thread_id"})
    assert not kind.relevant(message, {"external_id"})


def test_observed_fields_reject_reverse_relation_declarations() -> None:
    kind = CommentKind()
    kind.source_models = {"messaging.Thread": ("messages",)}
    with pytest.raises(ImproperlyConfigured, match=r"stored field: messaging.Thread.messages"):
        kind.relevant(apps.get_model("messaging", "Thread"), {"messages"})


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("activity_catalog")
@override_settings(ANGEE_TIMELINE_KINDS=KINDS)
def test_union_pages_sources_and_counts_the_whole_scope(composed_permissions: None) -> None:
    owner = create_platform_admin("timeline-owner")
    with open_changeset(fresh=True, change_reason="vault"):
        vault = vault_for(owner)
    with actor_context(owner):
        apps.get_model("projects", "Task").objects.schedule(vault.with_actor(owner), title="Follow up")
    for name in ("First edit", "Second edit"):
        with actor_context(owner), open_changeset(fresh=True, change_reason=name):
            vault.name = name
            vault.save(update_fields=["name"])

    with CaptureQueriesContext(connection) as queries:
        data = read_timeline(vault, owner)
    # The page, every kind's totals (read again by timeline_attention) and the
    # page's children share one statement over the kinds with readable rows.
    statements = [query["sql"] for query in queries if '"timeline_source"' in query["sql"]]
    assert len(statements) == 1
    assert "UNION ALL" in statements[0] and "LIMIT 26" in statements[0]
    assert f'"{apps.get_model("workflows", "WorkflowRun")._meta.db_table}"' not in statements[0]
    page = data["timeline"]
    assert [kind["id"] for kind in page["kinds"]] == [
        "workflows.run", "decisions.decision", "provenance.change", "projects.task", "messaging.conversation",
    ]
    counts = {kind["id"]: kind["count"] for kind in page["kinds"]}
    assert counts["provenance.change"] >= 3
    assert counts["workflows.run"] == 0
    assert counts["projects.task"] == 1
    assert page["entries"][0]["detail"] == "Second edit"
    assert page["entries"][0]["children"] == []
    assert page["needs_me"] == data["timeline_attention"] == 1
    one = read_timeline(vault, owner, limit=1)["timeline"]
    assert one["has_more"] and one["next_before"]
    rest = read_timeline(vault, owner, before=one["next_before"])["timeline"]
    assert [one["entries"][0], *rest["entries"]] == page["entries"]
    assert one["kinds"] == rest["kinds"] == page["kinds"]
    assert read_timeline(vault, owner, kinds=["workflows.run"])["timeline"]["entries"] == []
    with CaptureQueriesContext(connection) as queries:
        assert Timeline(vault, owner).attention() == 1
    assert sum('"timeline_source"' in query["sql"] for query in queries) == 1


# Reset sequences keep the bound identifiers, and so the SQL text, the same length in any run order.
@pytest.mark.django_db(transaction=True, reset_sequences=True)
@pytest.mark.usefixtures("activity_catalog")
@override_settings(ANGEE_TIMELINE_KINDS=KINDS)
def test_member_reading_by_grant_pays_a_fixed_rail_cost(composed_permissions: None) -> None:
    """A member granted read on the record reads the rail at a fixed cost, however many lines it has."""
    owner, member = create_user("timeline-grant-owner"), create_user("timeline-grant-member")
    with open_changeset(fresh=True, change_reason="vault"):
        vault = vault_for(owner)
    with actor_context(owner):
        vault.with_actor(owner).grant_record_access("viewer", member)
        apps.get_model("projects", "Task").objects.schedule(vault.with_actor(owner), title="Follow up")
    schema = addon_schema(timeline_schema.schemas, "console")
    variables = {"model": vault._meta.label, "id": public_id_of(vault)}

    def edit_then_read(edits: range) -> GraphQLDocumentRead:
        for index in edits:
            with actor_context(owner), open_changeset(fresh=True, change_reason=f"Edit {index}"):
                vault.name = f"Edit {index}"
                vault.save(update_fields=["name"])
        # Measured after one warm read, like the console document ceilings, on SQLite.
        measure_graphql_document(schema, QUERY, variables, actor=member)
        return measure_graphql_document(schema, QUERY, variables, actor=member)

    few = edit_then_read(range(2))
    counts = {kind["id"]: kind["count"] for kind in few.data["timeline"]["kinds"]}
    assert counts["provenance.change"] >= 3 and counts["projects.task"] == 1
    assert few.data["timeline"]["entries"][0]["detail"] == "Edit 1"
    assert few.data["timeline"]["needs_me"] == few.data["timeline_attention"] == 0
    few.assert_ceiling(statements=19, sql_kb=783)
    many = edit_then_read(range(2, 10))
    assert len(many.data["timeline"]["entries"]) == len(few.data["timeline"]["entries"]) + 8
    assert many.statements == few.statements
    many.assert_ceiling(statements=19, sql_kb=783)


def timeline_entries(record, actor=None, *, kind=None):
    """Read the service directly where a fixture already authorizes the record."""
    return Timeline(record, actor).page(kinds=[kind] if kind else None, limit=100).entries


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TIMELINE_KINDS=["angee.provenance.timeline.timeline_kinds"])
def test_existing_and_muted_source_changes_need_no_backfill(composed_permissions):
    owner = create_platform_admin("timeline-direct")
    with mute_changes(), actor_context(owner), open_changeset(fresh=True, change_reason="Before subscriptions"):
        record = vault_for(owner)
    first = read_timeline(record, owner)["timeline"]["entries"]
    assert len(first) == 1 and first[0]["detail"] == "Before subscriptions"
    with mute_changes(), actor_context(owner), open_changeset(fresh=True, change_reason="No callback"):
        record.name = "Current source"
        record.save(update_fields=["name"])
    assert read_timeline(record, owner)["timeline"]["entries"][0]["detail"] == "No callback"
    with pytest.raises(LookupError):
        apps.get_model("timeline", "Event")
