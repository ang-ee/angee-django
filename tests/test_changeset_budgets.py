"""Changeset bookkeeping cost, export hashing and the request boundary's query budget."""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from rebac import system_context

from angee.base.changesets import changeset, changeset_model_label, current_changeset
from angee.base.lineage import concrete_child_models
from angee.base.serialization import canonical_json_sha256
from angee.messaging.testing.models import Party
from tests.conftest import create_user


def _changeset_model() -> Any:
    return apps.get_model(changeset_model_label())


def _touching(captured: CaptureQueriesContext, model: Any) -> list[str]:
    table = connection.ops.quote_name(model._meta.db_table)
    return [query["sql"] for query in captured if table in query["sql"]]


def _data_statements(captured: CaptureQueriesContext) -> list[str]:
    """Each statement's verb and first table, without transaction control."""

    statements = []
    for query in captured:
        sql = query["sql"]
        if sql.startswith(("BEGIN", "COMMIT", "SAVEPOINT", "RELEASE")):
            continue
        table = re.search(r'(?:FROM|INTO|UPDATE)\s+"([^"]+)"', sql)
        statements.append(f"{sql.split(' ', 1)[0]} {table.group(1) if table else '?'}")
    return statements


@pytest.mark.django_db(transaction=True)
def test_close_writes_bookkeeping_only_and_export_records_the_hash(composed_tables: None) -> None:
    """Closing never re-reads the boundary's history; the export computes and records the digest."""

    del composed_tables
    owner = create_user("changeset-export-owner")
    with system_context(reason="test export"), changeset(fresh=True, change_reason="test.export"):
        Party._base_manager.create(display_name="Exported", created_by=owner)
        recorded = current_changeset(required=True, record=True)
        with pytest.raises(ValidationError, match="closed"):
            recorded.document()
    recorded.refresh_from_db()
    assert (recorded.state, recorded.outcome, recorded.hash) == ("closed", "success", "")
    assert recorded.touched == [Party._meta.label_lower]
    with system_context(reason="test export"):
        document = recorded.document()
        assert recorded.document() == document
    recorded.refresh_from_db()
    assert recorded.hash == canonical_json_sha256(chunks=(document,))
    assert f'"{Party._meta.label_lower}":[' in document and '"hash"' not in document


@pytest.mark.django_db(transaction=True)
def test_tracked_save_and_queryset_update_query_budgets(composed_tables: None) -> None:
    """One re-select and one history insert per captured statement, plus the savepoint pair."""

    del composed_tables
    owner = create_user("changeset-budget-owner")
    with system_context(reason="test budgets"), changeset(fresh=True, change_reason="test.budget"):
        party = Party._base_manager.create(display_name="Budget", created_by=owner)
        party.notes = "first"
        with CaptureQueriesContext(connection) as saved:
            party.save(update_fields=["notes", "updated_at"])
        with CaptureQueriesContext(connection) as updated:
            Party._base_manager.filter(pk=party.pk).update(first_met_note="met")
    assert not _touching(saved, _changeset_model()) and not _touching(updated, _changeset_model())
    party_table = Party._meta.db_table
    history_table = Party.history.model._meta.db_table
    # REBAC's schema-revision check, the UPDATE, then capture: one stored-state
    # re-select and one history insert.
    assert _data_statements(saved) == [
        "SELECT rebac_schemageneration",
        f"UPDATE {party_table}",
        f"SELECT {party_table}",
        f"INSERT {history_table}",
    ]
    # Frozen keys, REBAC's schema-revision check, the UPDATE, one re-select and
    # one history insert, then one key probe per tracked MTI child table.
    assert _data_statements(updated) == [
        f"SELECT {party_table}",
        "SELECT rebac_schemageneration",
        f"UPDATE {party_table}",
        f"SELECT {party_table}",
        f"INSERT {history_table}",
        # The capture's owner names the installed children: a class another module derived
        # from Party under isolate_apps stays a Python subclass until it is collected.
        *(f"SELECT {child._meta.db_table}" for child in concrete_child_models(Party)),
    ]


def _write_view(request: HttpRequest) -> HttpResponse:
    with system_context(reason="test request write"):
        Party._base_manager.create(display_name="Requested")
    return HttpResponse("written")


def _read_view(request: HttpRequest) -> HttpResponse:
    return HttpResponse("read")


urlpatterns = [path("write/", _write_view), path("read/", _read_view)]


@pytest.mark.django_db(transaction=True)
@override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=["rebac.middleware.ActorMiddleware", "angee.base.middleware.ChangesetMiddleware"],
)
def test_a_request_boundary_costs_one_insert_and_one_close(composed_tables: None) -> None:
    """A request persists its changeset on the first captured write and closes it once; reads cost nothing."""

    del composed_tables
    model = _changeset_model()
    before = model._base_manager.count()
    with CaptureQueriesContext(connection) as read:
        assert Client().get("/read/").status_code == 200
    assert not _touching(read, model) and model._base_manager.count() == before
    with CaptureQueriesContext(connection) as written:
        assert Client().get("/write/").status_code == 200
    recorded = model._base_manager.order_by("-pk").first()
    assert model._base_manager.count() == before + 1
    assert (recorded.state, recorded.outcome, recorded.touched) == ("closed", "success", [Party._meta.label_lower])
    statements = _touching(written, model)
    assert [sql.split(" ", 1)[0] for sql in statements] == ["INSERT", "UPDATE"], statements
