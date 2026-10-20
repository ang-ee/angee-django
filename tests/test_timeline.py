"""Record timeline reads exercise committed owner writes and actor-scoped SQL."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

import pytest
from django.apps import apps
from django.core.management import call_command
from django.test import override_settings
from rebac import actor_context

from angee.changesets import changeset as open_changeset
from angee.graphql.publishing import mute_changes
from angee.identity import public_id_of
from angee.timeline import schema as timeline_schema
from angee.timeline.events import sync_events
from angee.timeline.kinds import Cursor, timeline_kinds
from tests.conftest import addon_schema, create_platform_admin, execute_schema, result_data, vault_for

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
    cursor = Cursor.decode(Cursor(at=at, kind="provenance.change", pk=7).encode())
    assert cursor == Cursor(at=at, kind="provenance.change", pk=7)
    predicate = str(cursor.predicate())
    assert predicate.count("at__lt") == 1 and predicate.count("kind__gt") == 1 and predicate.count("pk__lt") == 1
    with pytest.raises(ValueError):
        Cursor.decode("nonsense")


@override_settings(ANGEE_TIMELINE_KINDS=KINDS)
def test_kinds_resolve_once_each_in_sequence_order() -> None:
    assert [kind.id for kind in timeline_kinds()] == [
        "workflows.run", "decisions.decision", "provenance.change", "messaging.comment", "projects.task",
        "messaging.conversation",
    ]


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TIMELINE_KINDS=KINDS)
def test_the_ledger_pages_committed_records_and_counts_the_whole_scope(composed_permissions: None) -> None:
    owner = create_platform_admin("timeline-owner")
    with open_changeset(fresh=True, change_reason="vault"):
        vault = vault_for(owner)
    for name in ("First edit", "Second edit"):
        with actor_context(owner), open_changeset(fresh=True, change_reason=name):
            vault.name = name
            vault.save(update_fields=["name"])

    data = read_timeline(vault, owner)
    page = data["timeline"]
    assert [kind["id"] for kind in page["kinds"]] == [
        "workflows.run", "decisions.decision", "provenance.change", "projects.task", "messaging.conversation",
    ]
    counts = {kind["id"]: kind["count"] for kind in page["kinds"]}
    assert counts["provenance.change"] >= 3
    assert counts["workflows.run"] == 0
    assert page["entries"][0]["detail"] == "Second edit"
    assert page["entries"][0]["children"] == []
    assert page["needs_me"] == data["timeline_attention"] == 0
    one = read_timeline(vault, owner, limit=1)["timeline"]
    assert one["has_more"] and one["next_before"]
    rest = read_timeline(vault, owner, before=one["next_before"])["timeline"]
    assert [one["entries"][0], *rest["entries"]] == page["entries"]
    assert one["kinds"] == rest["kinds"] == page["kinds"]
    assert read_timeline(vault, owner, kinds=["workflows.run"])["timeline"]["entries"] == []


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TIMELINE_KINDS=["angee.provenance.timeline.timeline_kinds"])
def test_backfill_reuses_the_projection_and_is_idempotent(composed_permissions: None) -> None:
    owner = create_platform_admin("timeline-backfill")
    with mute_changes(), open_changeset(fresh=True, change_reason="Before the ledger"):
        record = vault_for(owner)
    events = apps.get_model("timeline", "Event").objects.for_record(record)
    assert not events.exists()
    output = StringIO()
    call_command("timeline_backfill", kind=["provenance.change"], stdout=output)
    before = list(events.values())
    assert len(before) == 1
    changeset = apps.get_model("provenance", "Changeset")._base_manager.get(pk=before[0]["target_pk"])
    sync_events(changeset)
    call_command("timeline_backfill", kind=["provenance.change"], stdout=output)
    assert list(events.values()) == before
