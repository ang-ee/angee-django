"""The record timeline is one ledger: owners project their events; the read pages it behind one cursor."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.apps import apps
from django.test import override_settings

from angee.changesets import changeset as open_changeset
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
    kinds { id count models } needs_me has_more next_before
    entries { key at kind title actor detail needs_me target_model target_id children { kind count } }
  }
  timeline_attention(model: $model, id: $id)
}
"""


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
def test_the_ledger_lists_a_record_s_events_newest_first(composed_permissions: None) -> None:
    owner = create_platform_admin("owner")
    # A boundary of its own closes on exit; the close projects the vault's event.
    with open_changeset(fresh=True, change_reason="vault"):
        vault = vault_for(owner)
    event_model = apps.get_model("timeline", "Event")
    # The vault's creating changeset projected itself when it closed; a resync changes nothing.
    changeset = apps.get_model("provenance", "Changeset").objects.sudo(reason="test").touching(vault).latest("pk")
    assert event_model.objects.for_record(vault).filter(kind="provenance.change").exists()
    assert sync_events(changeset) == 0

    schema = addon_schema(timeline_schema.schemas, "console")
    variables = {"model": "knowledge.Vault", "id": public_id_of(vault)}
    data = result_data(execute_schema(schema, QUERY, variables, user=owner))
    page = data["timeline"]
    # A vault keeps history but is not threaded: the comment kind is absent, not empty.
    assert [kind["id"] for kind in page["kinds"]] == [
        "workflows.run", "decisions.decision", "provenance.change", "projects.task", "messaging.conversation",
    ]
    counts = {kind["id"]: kind["count"] for kind in page["kinds"]}
    assert counts["provenance.change"] >= 1 and counts["workflows.run"] == 0
    assert page["entries"][0]["kind"] == "provenance.change"
    assert page["entries"][0]["key"].startswith("provenance.change:")
    assert page["entries"][0]["target_model"] == "provenance.Changeset"
    assert page["entries"][0]["children"] == []
    ats = [entry["at"] for entry in page["entries"]]
    assert ats == sorted(ats, reverse=True)
    assert page["has_more"] is False and page["needs_me"] == 0 and data["timeline_attention"] == 0

    runs_only = {**variables, "kinds": ["workflows.run"]}
    scoped = result_data(execute_schema(schema, QUERY, runs_only, user=owner))["timeline"]
    assert scoped["entries"] == []
    assert [kind["id"] for kind in scoped["kinds"]] == [kind["id"] for kind in page["kinds"]]
    waiting = result_data(execute_schema(schema, QUERY, {**variables, "needsMe": True}, user=owner))["timeline"]
    assert waiting["entries"] == []

    one = result_data(execute_schema(schema, QUERY, {**variables, "limit": 1}, user=owner))["timeline"]
    if one["has_more"]:
        after = {**variables, "before": one["next_before"]}
        rest = result_data(execute_schema(schema, QUERY, after, user=owner))["timeline"]
        assert rest["entries"][0]["key"] != one["entries"][0]["key"]
