"""The record timeline unions every eligible source's one-liners behind one keyset cursor."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.test import override_settings

from angee.identity import public_id_of
from angee.timeline import schema as timeline_schema
from angee.timeline.sources import Cursor, TimelineEntry, timeline_sources
from tests.conftest import addon_schema, create_platform_admin, execute_schema, result_data, vault_for

SOURCES = [
    "angee.provenance.timeline.timeline_sources",
    "angee.decisions.timeline.timeline_sources",
    "angee.workflows.timeline.timeline_sources",
    "angee.messaging.timeline.timeline_sources",
]

QUERY = """
query($model: String!, $id: ID!, $kinds: [String!], $needsMe: Boolean, $before: String, $limit: Int) {
  timeline(model: $model, id: $id, kinds: $kinds, needs_me: $needsMe, before: $before, limit: $limit) {
    kinds { id count models } failed has_more next_before
    entries { key at kind title actor detail needs_me target_model target_id folded }
  }
}
"""


def test_cursor_orders_the_page_totally() -> None:
    at = datetime(2026, 10, 19, 9, 41, tzinfo=UTC)
    cursor = Cursor.decode(Cursor(at=at, kind="provenance.change", pk=7).encode())
    assert cursor == Cursor(at=at, kind="provenance.change", pk=7)
    same_kind_older_row = TimelineEntry(key="k1", at=at, kind="provenance.change", title="x", pk=6)
    same_kind_newer_row = TimelineEntry(key="k2", at=at, kind="provenance.change", title="x", pk=8)
    later_kind_same_instant = TimelineEntry(key="k3", at=at, kind="workflows.run", title="x", pk=99)
    earlier_kind_same_instant = TimelineEntry(key="k4", at=at, kind="decisions.decision", title="x", pk=1)
    assert cursor.admits(same_kind_older_row)
    assert not cursor.admits(same_kind_newer_row)
    assert cursor.admits(later_kind_same_instant)
    assert not cursor.admits(earlier_kind_same_instant)
    assert not cursor.admits(TimelineEntry(key="k5", at=at.replace(minute=42), kind="workflows.run", title="x", pk=1))
    assert str(cursor.predicate("workflows.run", "created_at")).count("created_at") == 2
    assert str(cursor.predicate("provenance.change", "occurred_at")).count("pk__lt") == 1
    assert str(cursor.predicate("decisions.decision", "answered_at")).count("answered_at") == 1


@override_settings(ANGEE_TIMELINE_SOURCES=SOURCES)
def test_sources_resolve_once_per_kind_in_sequence_order() -> None:
    assert [source.kind for source in timeline_sources()] == [
        "workflows.run", "decisions.decision", "provenance.change", "messaging.comment", "messaging.conversation",
    ]


@pytest.mark.django_db
@override_settings(ANGEE_TIMELINE_SOURCES=SOURCES)
def test_timeline_lists_eligible_kinds_newest_first(composed_permissions: None) -> None:
    owner = create_platform_admin("owner")
    vault = vault_for(owner)
    schema = addon_schema(timeline_schema.schemas, "console")
    variables = {"model": "knowledge.Vault", "id": public_id_of(vault)}
    page = result_data(execute_schema(schema, QUERY, variables, user=owner))["timeline"]
    # A vault keeps history but is not threaded: `messaging.comment` is absent, not empty.
    assert [kind["id"] for kind in page["kinds"]] == [
        "workflows.run", "decisions.decision", "provenance.change", "messaging.conversation",
    ]
    counts = {kind["id"]: kind["count"] for kind in page["kinds"]}
    assert counts["provenance.change"] >= 1
    assert page["failed"] == []
    assert page["entries"][0]["kind"] == "provenance.change"
    assert page["entries"][0]["key"].startswith("provenance.change:")
    assert page["entries"][0]["target_model"] == "provenance.Changeset"
    ats = [entry["at"] for entry in page["entries"]]
    assert ats == sorted(ats, reverse=True)
    assert page["has_more"] is False
    runs_only = {**variables, "kinds": ["workflows.run"]}
    scoped = result_data(execute_schema(schema, QUERY, runs_only, user=owner))["timeline"]
    assert scoped["entries"] == []
    assert [kind["id"] for kind in scoped["kinds"]] == [kind["id"] for kind in page["kinds"]]
    waiting = result_data(execute_schema(schema, QUERY, {**variables, "needsMe": True}, user=owner))["timeline"]
    assert waiting["entries"] == []
