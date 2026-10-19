"""The record timeline unions every eligible source's one-liners behind one cursor."""

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
query($model: String!, $id: ID!, $kinds: [String!], $before: String, $limit: Int) {
  timeline(model: $model, id: $id, kinds: $kinds, before: $before, limit: $limit) {
    kinds has_more next_before
    counts { kind count }
    entries { id at kind title actor detail needs_me target_model target_id }
  }
}
"""


def test_cursor_round_trip_and_tie_break() -> None:
    at = datetime(2026, 10, 19, 9, 41, tzinfo=UTC)
    cursor = Cursor.decode(Cursor(at=at, id="run_b").encode())
    assert cursor == Cursor(at=at, id="run_b")
    same_instant_lower = TimelineEntry(id="run_a", at=at, kind="run", title="x")
    same_instant_higher = TimelineEntry(id="run_c", at=at, kind="run", title="x")
    assert cursor.admits(same_instant_lower)
    assert not cursor.admits(same_instant_higher)
    assert not cursor.admits(TimelineEntry(id="run_z", at=at.replace(minute=42), kind="run", title="x"))


@override_settings(ANGEE_TIMELINE_SOURCES=SOURCES)
def test_sources_resolve_once_per_kind() -> None:
    kinds = [source.kind for source in timeline_sources()]
    assert kinds == ["change", "decision", "run", "comment", "conversation"]


@pytest.mark.django_db
@override_settings(ANGEE_TIMELINE_SOURCES=SOURCES)
def test_timeline_lists_eligible_kinds_newest_first(composed_permissions: None) -> None:
    owner = create_platform_admin("owner")
    vault = vault_for(owner)
    schema = addon_schema(timeline_schema.schemas, "console")
    variables = {"model": "knowledge.Vault", "id": public_id_of(vault)}
    page = result_data(execute_schema(schema, QUERY, variables, user=owner))["timeline"]
    # A vault keeps history but is not threaded: `comment` is absent, not empty.
    assert page["kinds"] == ["change", "decision", "run", "conversation"]
    counts = {row["kind"]: row["count"] for row in page["counts"]}
    assert counts["change"] >= 1
    assert page["entries"][0]["kind"] == "change"
    assert page["entries"][0]["target_model"] == "provenance.Changeset"
    ats = [entry["at"] for entry in page["entries"]]
    assert ats == sorted(ats, reverse=True)
    assert page["has_more"] is False
    scoped = result_data(execute_schema(schema, QUERY, {**variables, "kinds": ["run"]}, user=owner))["timeline"]
    assert scoped["entries"] == []
    assert scoped["kinds"] == page["kinds"]
