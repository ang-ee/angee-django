"""Record timeline reads exercise committed owner writes and actor-scoped SQL."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.base.models.identity import public_id_of
from angee.base.pagination import KeysetOrder, keyset_signer
from angee.decisions.timeline import DecisionKind
from angee.graphql.publishing import mute_changes
from angee.messaging.testing.models import Message, ThreadAttachment
from angee.messaging.timeline import CommentKind
from angee.provenance.context import changeset as open_changeset
from angee.timeline import schema as timeline_schema
from angee.timeline.kinds import timeline_kinds
from angee.timeline.reading import Timeline
from tests.conftest import addon_schema, create_platform_admin, execute_schema, result_data, vault_for
from tests.messaging_campaign import comment_record as comment_record

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
    order = KeysetOrder("_timeline_at", tie_breakers=("_timeline_kind",))
    signer = keyset_signer(["record", "all-kinds"], salt="angee.timeline")
    token = order.sign_position((at, "provenance.change", 7), signer)
    cursor = order.unsign(token, signer, models.BigIntegerField())
    assert cursor == (at, "provenance.change", 7)
    predicate = str(order.before(cursor))
    assert predicate.count("at__lt") == 1 and predicate.count("kind__gt") == 1 and predicate.count("pk__lt") == 1
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
def test_decision_timeline_without_requester_contributors(composed_permissions: None, monkeypatch) -> None:
    actor = create_platform_admin("decision-only-reader")
    record = vault_for(actor)
    decision = apps.get_model("decisions", "Decision")
    monkeypatch.setattr(decision, "requester_querysets", classmethod(lambda cls: ()))
    rows, fold = DecisionKind().source(record, actor)
    assert fold is None
    assert list(rows) == []


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
    page_queries = [query["sql"] for query in queries if "UNION ALL" in query["sql"] and "LIMIT 26" in query["sql"]]
    assert len(page_queries) == 1
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
    tasks = [entry for entry in page["entries"] if entry["kind"] == "projects.task"]
    assert len(tasks) == 1 and tasks[0]["needs_me"] is True
    assert all(entry["needs_me"] is False for entry in page["entries"]
               if entry["kind"] == "provenance.change")
    attention = read_timeline(vault, owner, needsMe=True)["timeline"]
    assert [entry["target_id"] for entry in attention["entries"]] == [tasks[0]["target_id"]]
    assert attention["kinds"] == page["kinds"]
    one = read_timeline(vault, owner, limit=1)["timeline"]
    assert one["has_more"] and one["next_before"]
    clamped = Timeline(vault, owner).page(limit=0)
    assert len(clamped.entries) == 1 and clamped.has_more
    assert {kind.id: kind.count for kind in clamped.kinds} == counts
    assert clamped.needs_me == page["needs_me"]
    rest = read_timeline(vault, owner, before=one["next_before"])["timeline"]
    assert [one["entries"][0], *rest["entries"]] == page["entries"]
    assert one["kinds"] == rest["kinds"] == page["kinds"]
    assert read_timeline(vault, owner, kinds=["workflows.run"])["timeline"]["entries"] == []


@pytest.mark.postgresql
@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("activity_catalog")
@override_settings(ANGEE_TIMELINE_KINDS=KINDS)
def test_sparse_non_admin_page_has_one_bounded_selection_statement(comment_record, composed_permissions: None) -> None:
    record, actor = comment_record.record, comment_record.author
    assert not actor.is_superuser
    with system_context(reason="test.timeline.selection.guard"):
        attachment = ThreadAttachment.objects.ensure_for_record(record)
        Message.objects.create(
            thread=attachment.thread, message_type="comment", direction="internal", created_by=actor,
        )
    with CaptureQueriesContext(connection) as queries:
        timeline = Timeline(record, actor)
        page = timeline.page()
    assert {kind.id for kind in timeline.kinds} == {kind.id for kind in timeline_kinds()}
    assert next(kind.count for kind in page.kinds if kind.id == "workflows.run") == 0
    selections = [
        query["sql"] for query in queries
        if 'WITH RECURSIVE "timeline_source" AS' in query["sql"]
    ]
    assert len(selections) == 1
    sql, = selections
    source_kinds = re.findall(r"'([\w.]+)'(?:::\w+)? AS \"_timeline_kind\"", sql)
    # With no linked run candidates, its actor-scoped branch compiles to EMPTY;
    # the public kind still appears with count zero.
    assert sorted(source_kinds) == sorted(kind.id for kind in timeline.kinds if kind.id != "workflows.run")
    assert re.findall(r"'(\w+)'(?:::\w+)? AS \"_timeline_row\"", sql) == ["kind", "entry", "child"]
    assert len(sql.encode("utf-8")) <= 275_000
    assert all(len(query["sql"].encode("utf-8")) <= 275_000 for query in queries)


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
