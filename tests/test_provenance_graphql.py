"""The provenance history surface: shape, read gate, redaction, statements and revert.

``history`` reads a record's changesets through the record's own ``read`` gate;
``revert_changeset`` restores through its ``write`` gate. Knowledge pages carry
the vault-scoped gate, the ``scopedemo`` probe row carries a field-gated
``title``, and a party asserts statements on save.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from rebac import RelationshipTuple, actor_context, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import reset_backend
from rebac.schema import parse_zed

from angee.base.changesets import changeset
from angee.base.identity import public_id_of
from angee.knowledge import schema as knowledge_schema
from angee.parties import schema as parties_schema
from angee.provenance import schema as provenance_schema
from angee.testing.permissions import install_permission_schema
from tests.conftest import Page, addon_schema, create_user, execute_schema, result_data, vault_for
from tests.core_persistence import OwnedRow

pytestmark = pytest.mark.usefixtures("composed_tables")

Party = apps.get_model("parties", "Party")

HISTORY = """
query History($model: String!, $id: ID!, $limit: Int, $offset: Int) {
  history(model: $model, id: $id, limit: $limit, offset: $offset) {
    model id total
    entries {
      id actor actor_model actor_id source_kind action activity change_reason outcome can_revert
      changes { field old new }
      statements { aspect asserted_by source_kind status applied history_type values }
      merge { survivor { model id retained } absorbed { model id retained } }
    }
  }
}
"""

REVERT = """
mutation Revert($model: String!, $id: ID!, $changeset: ID!) {
  revert_changeset(model: $model, id: $id, changeset: $changeset) { ok message id }
}
"""


def _schema() -> Any:
    return addon_schema(provenance_schema.schemas, "console")


def _history(schema: Any, user: Any, model: str, record: Any, **page: Any) -> Any:
    result = execute_schema(schema, HISTORY, {"model": model, "id": public_id_of(record), **page}, user=user)
    return result_data(result)["history"]


def _changes(entry: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    return {change["field"]: (change["old"], change["new"]) for change in entry["changes"]}


def _titled_page(owner: Any, *, vault_name: str = "Research") -> Any:
    """A folder page: saved as a ``Page`` row, so its creation is its first version."""

    vault = vault_for(owner, name=vault_name)
    with actor_context(owner), changeset(fresh=True, change_reason="Draft"):
        page = Page.objects.create_in(vault, kind=Page.PageKind.FOLDER, title="Draft title")
    with actor_context(owner), changeset(fresh=True, change_reason="Rename"):
        page.title = "Final title"
        page.save()
    return vault, page


def test_history_lists_a_records_changesets_newest_first_with_their_field_deltas() -> None:
    owner = create_user("history-owner")
    _vault, page = _titled_page(owner)

    history = _history(_schema(), owner, "knowledge.Page", page)

    assert history["model"] == "knowledge.Page"
    assert history["id"] == public_id_of(page)
    assert history["total"] == 2
    rename, draft = history["entries"]
    assert [entry["change_reason"] for entry in (rename, draft)] == ["Rename", "Draft"]
    assert rename["actor"] == str(to_subject_ref(owner))
    assert rename["actor_model"] == get_user_model()._meta.label
    assert rename["actor_id"] == public_id_of(owner)
    assert rename["outcome"] == "SUCCESS"
    assert rename["can_revert"] is True
    assert rename["merge"] is None
    assert _changes(rename) == {"title": ("Draft title", "Final title")}
    assert _changes(draft)["title"] == ("", "Draft title")
    assert _changes(draft)["vault"][1] == public_id_of(_vault)


def test_history_pages_by_changeset_and_refuses_an_unbounded_page() -> None:
    owner = create_user("history-pager")
    _vault, page = _titled_page(owner)
    schema = _schema()

    second = _history(schema, owner, "knowledge.Page", page, limit=1, offset=1)
    assert second["total"] == 2
    assert [entry["change_reason"] for entry in second["entries"]] == ["Draft"]

    refused = execute_schema(
        schema,
        HISTORY,
        {"model": "knowledge.Page", "id": public_id_of(page), "limit": provenance_schema.HISTORY_LIMIT_MAX + 1},
        user=owner,
    )
    assert refused.errors and "limit" in refused.errors[0].message


def test_history_follows_the_records_read_gate_and_write_decides_revert() -> None:
    owner = create_user("gate-owner")
    viewer = create_user("gate-viewer")
    outsider = create_user("gate-outsider")
    vault, page = _titled_page(owner)
    write_relationships(
        [RelationshipTuple(resource=to_object_ref(vault), relation="viewer", subject=to_subject_ref(viewer))]
    )
    schema = _schema()

    assert _history(schema, outsider, "knowledge.Page", page) is None
    seen = _history(schema, viewer, "knowledge.Page", page)
    assert seen["total"] == 2
    assert all(entry["can_revert"] is False for entry in seen["entries"])


@pytest.mark.parametrize("label", ["knowledge.Nope", "knowledge.Link", "auth.Group"])
def test_history_refuses_models_without_a_gated_history(label: str) -> None:
    owner = create_user("history-unknown")

    result = execute_schema(_schema(), HISTORY, {"model": label, "id": "x"}, user=owner)

    assert result.errors
    assert "history" in result.errors[0].message or "Unknown model" in result.errors[0].message


def test_revert_restores_the_record_through_write_and_records_a_revert_changeset() -> None:
    owner = create_user("revert-owner")
    viewer = create_user("revert-viewer")
    vault, page = _titled_page(owner)
    write_relationships(
        [RelationshipTuple(resource=to_object_ref(vault), relation="viewer", subject=to_subject_ref(viewer))]
    )
    schema = _schema()
    rename = _history(schema, owner, "knowledge.Page", page)["entries"][0]
    variables = {"model": "knowledge.Page", "id": public_id_of(page), "changeset": rename["id"]}

    denied = result_data(execute_schema(schema, REVERT, variables, user=viewer))["revert_changeset"]
    assert denied["ok"] is False
    page.refresh_from_db()
    assert page.title == "Final title"

    reverted = result_data(execute_schema(schema, REVERT, variables, user=owner))["revert_changeset"]
    assert reverted == {"ok": True, "message": "Changeset reverted.", "id": reverted["id"]}
    page.refresh_from_db()
    assert page.title == "Draft title"
    history = _history(schema, owner, "knowledge.Page", page)
    assert history["total"] == 3
    newest = history["entries"][0]
    assert newest["id"] == reverted["id"]
    assert newest["activity"] == "REVERT"
    assert _changes(newest) == {"title": ("Final title", "Draft title")}


def test_revert_refuses_a_changeset_that_did_not_touch_the_record() -> None:
    owner = create_user("revert-stranger")
    _vault, page = _titled_page(owner)
    _other_vault, other = _titled_page(owner, vault_name="Other")
    schema = _schema()
    foreign = _history(schema, owner, "knowledge.Page", other)["entries"][0]

    refused = result_data(
        execute_schema(
            schema,
            REVERT,
            {"model": "knowledge.Page", "id": public_id_of(page), "changeset": foreign["id"]},
            user=owner,
        )
    )["revert_changeset"]

    assert refused["ok"] is False
    assert "did not touch" in refused["message"]


@pytest.fixture
def gated_title() -> Any:
    """The persistence probe with a ``title`` only a ``title_reader`` may read."""

    install_permission_schema(
        parse_zed(
            """
            definition auth/user {}
            definition scopedemo/owned_row {
                relation owner: auth/user // rebac:field=owner
                relation title_reader: auth/user
                permission create = authenticated
                permission read = owner
                permission write = owner
                permission delete = owner
                permission read__title = title_reader
            }
            """
        )
    )
    try:
        yield
    finally:
        reset_backend()


def test_history_redacts_the_fields_the_actor_cannot_read_on_the_record(gated_title: None) -> None:
    owner = create_user("redaction-owner")
    with actor_context(owner), changeset(fresh=True, change_reason="Create"):
        row = OwnedRow.objects.create(title="Stored secret")
    with actor_context(owner), changeset(fresh=True, change_reason="Edit"):
        row.title = "Edited secret"
        row.save()
    schema = _schema()

    hidden = _history(schema, owner, "scopedemo.OwnedRow", row)
    assert hidden["total"] == 2
    assert all("title" not in _changes(entry) for entry in hidden["entries"])

    write_relationships(
        [RelationshipTuple(resource=to_object_ref(row), relation="title_reader", subject=to_subject_ref(owner))]
    )
    shown = _history(schema, owner, "scopedemo.OwnedRow", row)
    assert _changes(shown["entries"][0])["title"] == ("Stored secret", "Edited secret")


def test_history_carries_the_statements_a_changeset_asserted_for_the_record() -> None:
    person = create_user("statement-person")
    with actor_context(person), changeset(fresh=True, change_reason="Create party"):
        party = Party.objects.create(display_name="Ada Lovelace")

    history = _history(_schema(), person, "parties.Party", party)

    created = history["entries"][-1]
    identity = [row for row in created["statements"] if row["aspect"] == "identity"]
    # Asserted first, then applied by survivorship: two versions in one changeset.
    assert [(row["history_type"], row["applied"]) for row in identity] == [("+", False), ("~", True)]
    assert {row["asserted_by"] for row in identity} == {str(to_subject_ref(person))}
    assert {row["source_kind"] for row in identity} == {"PERSON"}
    assert {row["status"] for row in identity} == {"asserted"}
    assert identity[-1]["values"] == {"display_name": "Ada Lovelace"}


def test_metadata_marks_a_resource_that_keeps_history() -> None:
    """A gated, tracked record is flagged; a statement shadow table, read through its subject, is not."""

    schema = addon_schema(knowledge_schema.schemas, "console", parties_schema.schemas)
    resources = {item.model_label: item for item in schema.angee_resources}
    assert resources["knowledge.Page"].history is True
    assert resources["parties.Party"].history is True
    assert resources["parties.PartyStatement"].history is False
    wire = {item["modelLabel"]: item for item in schema._schema.extensions["angee"]["resources"]}
    assert wire["knowledge.Page"]["history"] is True
