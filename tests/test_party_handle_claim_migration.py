"""The party-handle claim transition: one claim per existing link, reversibly."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.parties.runtime_migrations import party_handle_claims
from tests.tables import model_tables
from tests.test_runtime_migrations import isolated_upgrade_database as isolated_upgrade_database


@pytest.fixture
def historical_links(transactional_db, isolated_upgrade_database):
    """Links as they stood before claims: one source, one score and the evidence on the link."""

    state = ProjectState()
    label, name = settings.AUTH_USER_MODEL.split(".")
    state.add_model(ModelState(label, name, [("id", models.AutoField(primary_key=True))]))
    state.add_model(ModelState("parties", "Party", [("id", models.AutoField(primary_key=True))]))
    state.add_model(ModelState("parties", "Handle", [("id", models.AutoField(primary_key=True))]))
    state.add_model(ModelState("parties", "PartyHandle", [
        ("id", models.AutoField(primary_key=True)),
        ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE)),
        ("handle", models.ForeignKey("parties.Handle", on_delete=models.CASCADE)),
        ("source", models.CharField(max_length=11, default="manual")),
        ("confidence", models.FloatField(default=1.0)),
        ("is_confirmed", models.BooleanField(default=False)),
        ("is_dismissed", models.BooleanField(default=False)),
        ("metadata", models.JSONField(default=dict)),
        ("created_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)),
    ], options={
        "constraints": [models.UniqueConstraint(fields=("party", "handle"), name="uq_party_handle")],
    }))
    historical = [state.apps.get_model(app, model) for app, model in (
        (label, name), ("parties", "Party"), ("parties", "Handle"), ("parties", "PartyHandle"),
    )]
    with model_tables(tuple(historical)):
        yield state


def test_each_link_becomes_one_claim_of_its_source_and_reverses(historical_links):
    before = historical_links
    user = before.apps.get_model(settings.AUTH_USER_MODEL).objects.create()
    party = before.apps.get_model("parties", "Party").objects.create()
    handles = [before.apps.get_model("parties", "Handle").objects.create() for _ in range(3)]
    links = before.apps.get_model("parties", "PartyHandle")
    rows = [
        ("rule", 0.3, False, {"evidence": {"kind": "signature_phone", "fragment_hash": "f"}}),
        ("import", 1.0, True, {"provenance": "linkedin_takeout"}),
        ("carddav", 1.0, False, {}),
    ]
    for handle, (source, confidence, confirmed, metadata) in zip(handles, rows, strict=True):
        links.objects.create(
            party=party, handle=handle, source=source, confidence=confidence,
            is_confirmed=confirmed, metadata=metadata, created_by=user,
        )
    original = list(links.objects.order_by("pk").values())
    assert party_handle_claims.applies(before)

    migration = party_handle_claims.Migration("party_handle_claims", "parties")
    with connection.schema_editor() as editor:
        after = migration.apply(before.clone(), editor)
    claims = after.apps.get_model("parties", "PartyHandleClaim")
    assert list(claims.objects.order_by("link_id").values_list("link_id", "source", "confidence", "metadata")) == [
        (row["id"], row["source"], row["confidence"], row["metadata"]) for row in original
    ]
    assert set(claims.objects.values_list("created_by_id", flat=True)) == {user.pk}
    assert "metadata" not in after.models["parties", "partyhandle"].fields
    assert not party_handle_claims.applies(after)

    with connection.schema_editor() as editor:
        migration.unapply(before, editor)
    assert list(links.objects.order_by("pk").values()) == original
