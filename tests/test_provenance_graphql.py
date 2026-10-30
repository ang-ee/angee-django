"""The provenance history surface: shape, read gate, redaction, statements and revert.

``history`` reads a record's changesets through the record's own ``read`` gate;
``revert_changeset`` restores through its ``write`` gate. Knowledge pages carry
the vault-scoped gate, the ``scopedemo`` probe row carries a field-gated
``title``, and a party asserts statements on save.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import reset_backend
from rebac.schema import parse_zed
from rebac.testing import install_schema

from angee.base.identity import public_subject_ref
from angee.base.models.base import AngeeDataModel
from angee.base.models.identity import public_id_of
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.knowledge import schema as knowledge_schema
from angee.messaging.testing.models import Channel
from angee.parties import schema as parties_schema
from angee.provenance import schema as provenance_schema
from angee.provenance.context import changeset
from angee.provenance.evidence import EvidenceReference
from angee.provenance.statements import (
    SourceKind,
    StatementLinkMixin,
)
from tests.conftest import (
    Page,
    addon_schema,
    create_user,
    current_stream,
    execute_schema,
    make_integration,
    result_data,
    vault_for,
)
from tests.core_persistence import OwnedRow

pytestmark = pytest.mark.usefixtures("composed_tables")

Party = apps.get_model("parties", "Party")
Handle = apps.get_model("parties", "Handle")

HISTORY = """
query History($model: String!, $id: ID!, $limit: Int, $offset: Int, $selected: ID) {
  history(model: $model, id: $id, limit: $limit, offset: $offset, selected: $selected) {
    model id total offset
    entries {
      id actor actor_model actor_id source_kind action activity change_reason outcome can_revert
      changes { model id field old new }
      statements {
        aspect asserted_by source_kind status applied history_type values
        source { model id label pointer }
        evidence { model id label pointer }
      }
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

FIELD_PROVENANCE = """
query FieldProvenance($model: String!, $id: ID!) {
  field_provenance(model: $model, id: $id) {
    field authority origin statement_model statement_id changeset_id
  }
}
"""


def _schema() -> Any:
    return addon_schema(provenance_schema.schemas, "console")


def _history(schema: Any, user: Any, model: str, record: Any, **page: Any) -> Any:
    result = execute_schema(schema, HISTORY, {"model": model, "id": public_id_of(record), **page}, user=user)
    return result_data(result)["history"]


def _field_provenance(schema: Any, user: Any, model: str, record: Any) -> Any:
    result = execute_schema(schema, FIELD_PROVENANCE, {"model": model, "id": public_id_of(record)}, user=user)
    return result_data(result)["field_provenance"]


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
    assert rename["actor"] == str(public_subject_ref(to_subject_ref(owner)))
    assert rename["actor_model"] == get_user_model()._meta.label
    assert rename["actor_id"] == public_id_of(owner)
    assert rename["outcome"] == "SUCCESS"
    assert rename["can_revert"] is True
    assert rename["merge"] is None
    assert _changes(rename) == {"title": ("Draft title", "Final title")}
    assert _changes(draft)["title"] == ("", "Draft title")
    assert _changes(draft)["vault"][1] == public_id_of(_vault)


@pytest.mark.usefixtures("activity_catalog", "composed_permissions")
def test_history_keeps_foreign_keys_to_named_fields_as_stored_values() -> None:
    owner = create_user("history-named-reference")
    vault = vault_for(owner)
    with actor_context(owner), changeset(fresh=True, change_reason="Schedule"):
        task = apps.get_model("projects", "Task").objects.schedule(vault.with_actor(owner), title="Follow up")
    with actor_context(owner), changeset(fresh=True, change_reason="Change activity"):
        task.activity_type_id = "call"
        task.save(update_fields=["activity_type"])

    updated, created = _history(_schema(), owner, "projects.Task", task)["entries"]
    assert _changes(updated)["activity_type"] == ("todo", "call")
    assert _changes(created)["activity_type"] == (None, "todo")
    assert _changes(created)["assignee"] == (None, public_id_of(owner))


def test_history_pages_by_changeset_and_refuses_an_unbounded_page() -> None:
    owner = create_user("history-pager")
    _vault, page = _titled_page(owner)
    schema = _schema()

    second = _history(schema, owner, "knowledge.Page", page, limit=1, offset=1)
    assert second["total"] == 2
    assert second["offset"] == 1
    assert [entry["change_reason"] for entry in second["entries"]] == ["Draft"]

    refused = execute_schema(
        schema,
        HISTORY,
        {"model": "knowledge.Page", "id": public_id_of(page), "limit": provenance_schema.HISTORY_LIMIT_MAX + 1},
        user=owner,
    )
    assert refused.errors and "limit" in refused.errors[0].message


def test_history_locates_an_older_selected_changeset_within_the_records_own_pages() -> None:
    owner = create_user("history-selection-owner")
    _vault, page = _titled_page(owner)
    schema = _schema()
    draft = _history(schema, owner, "knowledge.Page", page)["entries"][-1]["id"]
    with actor_context(owner):
        for number in range(provenance_schema.HISTORY_LIMIT_DEFAULT):
            with changeset(fresh=True, change_reason=f"Later {number}"):
                page.title = f"Later title {number}"
                page.save()

    selected = _history(schema, owner, "knowledge.Page", page, selected=draft)
    assert selected["offset"] == provenance_schema.HISTORY_LIMIT_DEFAULT
    assert selected["total"] == provenance_schema.HISTORY_LIMIT_DEFAULT + 2
    assert [entry["id"] for entry in selected["entries"]][-1] == draft
    # Explicit offset paging remains available after resolving the initial target.
    assert _history(schema, owner, "knowledge.Page", page, offset=0)["offset"] == 0


def test_history_selection_never_follows_an_unrelated_or_unreadable_record() -> None:
    owner = create_user("history-selection-reader")
    outsider = create_user("history-selection-outsider")
    _vault, page = _titled_page(owner)
    _other_vault, other = _titled_page(outsider)
    _readable_vault, readable_other = _titled_page(owner, vault_name="Other readable history")
    schema = _schema()
    foreign = _history(schema, outsider, "knowledge.Page", other)["entries"][0]["id"]
    original = _history(schema, owner, "knowledge.Page", page, limit=1, offset=1)
    assert _history(schema, owner, "knowledge.Page", page, limit=1, offset=1, selected=foreign) == original
    readable_foreign = _history(schema, owner, "knowledge.Page", readable_other)["entries"][0]["id"]
    assert _history(schema, owner, "knowledge.Page", page, limit=1, offset=1, selected=readable_foreign) == original
    assert _history(schema, owner, "knowledge.Page", other, selected=foreign) is None
    assert _history(schema, outsider, "knowledge.Page", page, selected=foreign) is None


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
    assert "complete write boundary" in refused["message"]


@pytest.fixture
def gated_title() -> Any:
    """The persistence probe with a ``title`` only a ``title_reader`` may read."""

    install_schema(
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
    assert [(row["history_type"], row["applied"]) for row in identity] == [
        ("CREATED", False),
        ("CHANGED", True),
    ]
    assert {row["asserted_by"] for row in identity} == {str(to_subject_ref(person))}
    assert {row["source_kind"] for row in identity} == {"PERSON"}
    assert {row["status"] for row in identity} == {"ASSERTED"}
    assert {row["source"]["id"] for row in identity} == {public_id_of(person)}
    assert all(row["evidence"] == [] for row in identity)
    assert identity[-1]["values"] == {"display_name": "Ada Lovelace"}


def test_field_provenance_decorates_a_partys_stated_field_through_the_statement_read_gate() -> None:
    person = create_user("field-provenance-person")
    outsider = create_user("field-provenance-outsider")
    with actor_context(person), changeset(fresh=True, change_reason="Create party"):
        party = Party.objects.create(display_name="Ada Lovelace")
    statement = Party.statements.model._base_manager.get(subject=party, aspect="identity")
    schema = _schema()

    assert _field_provenance(schema, person, "parties.Party", party) == [
        {
            "field": "display_name",
            "authority": "CORRECTION",
            "origin": str(public_subject_ref(to_subject_ref(person))),
            "statement_model": "parties.PartyStatement",
            "statement_id": public_id_of(statement),
            "changeset_id": public_id_of(statement.changeset),
        }
    ]
    assert _field_provenance(schema, outsider, "parties.Party", party) is None


def test_field_provenance_is_empty_for_a_readable_record_without_statements() -> None:
    person = create_user("field-provenance-history-only")
    _vault, page = _titled_page(person)

    assert _field_provenance(_schema(), person, "knowledge.Page", page) == []


def test_history_projects_a_readable_statement_link_and_evidence_inline() -> None:
    """Activity gets each statement's retained connector identity and readable evidence in one page."""

    bridge = make_integration("history-statement-source", model=Channel)
    owner = bridge.owner
    with actor_context(owner), changeset(fresh=True, change_reason="Create sourced party"):
        party = Party.objects.create(display_name="Source party")
    with system_context(reason="tests.history.statement_source"):
        stream = current_stream(bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
        link = RecordLink.objects.observe(stream, "contact:1", target=party)
    with system_context(reason="tests.history.statement_source"), changeset(
        fresh=True,
        change_reason="Import identity",
    ):
        Party.statements.model._base_manager.create(
            subject=party,
            aspect="identity",
            asserted_by=str(to_subject_ref(bridge)),
            source_kind=SourceKind.SYSTEM_OF_RECORD,
            link=link,
            evidence=[EvidenceReference.of(party, pointer="/display_name").model_dump()],
            display_name="Imported name",
        )

    history = _history(_schema(), owner, "parties.Party", party)
    imported = next(entry for entry in history["entries"] if entry["change_reason"] == "Import identity")
    statement = imported["statements"][0]
    assert statement["source"] == {
        "model": "integrate.RecordLink",
        "id": public_id_of(link),
        "label": f"{bridge.record_display_label} · contacts · contact:1",
        "pointer": "",
    }
    assert statement["evidence"] == [{
        "model": "parties.Party",
        "id": public_id_of(party),
        "label": "Source party",
        "pointer": "/display_name",
    }]


@pytest.mark.parametrize("row_count", [1, 25])
def test_history_page_query_count_does_not_grow_per_entry(
    row_count: int,
    django_assert_num_queries: Any,
) -> None:
    """Loaded versions, statements, redaction and admission stay page-batched."""

    person = create_user(f"history-budget-{row_count}")
    with actor_context(person), changeset(fresh=True, change_reason="Create party"):
        party = Party.objects.create(display_name="Version 0")
    for index in range(1, 25):
        with actor_context(person), changeset(fresh=True, change_reason=f"Version {index}"):
            party.display_name = f"Version {index}"
            party.save()

    schema = _schema()
    _history(schema, person, "parties.Party", party, limit=1)
    with CaptureQueriesContext(connection) as baseline:
        _history(schema, person, "parties.Party", party, limit=1)
    with django_assert_num_queries(len(baseline)):
        page = _history(schema, person, "parties.Party", party, limit=row_count)

    assert len(page["entries"]) == row_count


def test_history_names_a_statements_related_row_by_its_public_id() -> None:
    person = create_user("statement-handle-person")
    with actor_context(person), changeset(fresh=True, change_reason="Create party"):
        party = Party.objects.create(display_name="Grace Hopper")
    with system_context(reason="tests.history.handle_statement"), changeset(fresh=True, change_reason="Add handle"):
        handle = Handle.objects.create(platform="email", value="grace@example.com", created_by_id=person.pk)
        Party.statements.model._base_manager.create(
            subject=party, handle=handle, aspect="handle",
            asserted_by=str(to_subject_ref(person)), source_kind=SourceKind.PERSON,
        )

    history = _history(_schema(), person, "parties.Party", party)

    handles = [row for entry in history["entries"] for row in entry["statements"] if row["aspect"] == "handle"]
    assert handles
    assert {tuple(row["values"].items()) for row in handles} == {(("handle", public_id_of(handle)),)}


def test_metadata_marks_a_resource_that_keeps_history() -> None:
    """A gated, tracked record is flagged; a statement shadow table, read through its subject, is not."""

    schema = addon_schema(knowledge_schema.schemas, "console", parties_schema.schemas, provenance_schema.schemas)
    resources = {item.model_label: item for item in schema.angee_resources}
    assert resources["knowledge.Page"].history is True
    assert resources["parties.Party"].history is True
    assert resources["parties.PartyStatement"].history is False
    wire = {item["modelLabel"]: item for item in schema._schema.extensions["angee"]["resources"]}
    assert wire["knowledge.Page"]["history"] is True


def test_document_history_includes_owned_lines_and_reverts_the_complete_edit() -> None:
    owner = create_user("document-history-owner")
    project_model = apps.get_model("projects", "Project")
    milestone_model = apps.get_model("projects", "Milestone")
    with actor_context(owner), changeset(fresh=True, change_reason="Plan"):
        project = project_model.objects.create(title="Original plan", owner=owner)
        milestone = milestone_model.objects.create(project=project, name="Original phase")
    with actor_context(owner), changeset(fresh=True, change_reason="Revise plan"):
        project.title = "Revised plan"
        project.save(update_fields=("title",))
        milestone.name = "Revised phase"
        milestone.save(update_fields=("name",))
    schema = _schema()
    entry = _history(schema, owner, "projects.Project", project)["entries"][0]
    changes = {(row["model"], row["id"], row["field"]): (row["old"], row["new"]) for row in entry["changes"]}
    assert changes["projects.Project", public_id_of(project), "title"] == ("Original plan", "Revised plan")
    assert changes["projects.Milestone", public_id_of(milestone), "name"] == ("Original phase", "Revised phase")
    assert entry["can_revert"] is True
    result = result_data(execute_schema(schema, REVERT, {
        "model": "projects.Project", "id": public_id_of(project), "changeset": entry["id"],
    }, user=owner))["revert_changeset"]
    assert result["ok"] is True
    project.refresh_from_db()
    milestone.refresh_from_db()
    assert (project.title, milestone.name) == ("Original plan", "Original phase")


def test_record_history_refuses_reverting_a_boundary_that_also_changes_another_record() -> None:
    owner = create_user("history-boundary-owner")
    _vault, page = _titled_page(owner)
    _other_vault, other = _titled_page(owner, vault_name="Other document")
    with actor_context(owner), changeset(fresh=True, change_reason="Joint edit"):
        for record in (page, other):
            record.title = "Joint edit"
            record.save(update_fields=("title",))
    schema = _schema()
    entry = _history(schema, owner, "knowledge.Page", page)["entries"][0]
    assert entry["can_revert"] is False
    result = result_data(execute_schema(schema, REVERT, {
        "model": "knowledge.Page", "id": public_id_of(page), "changeset": entry["id"],
    }, user=owner))["revert_changeset"]
    assert result["ok"] is False
    page.refresh_from_db()
    other.refresh_from_db()
    assert page.title == other.title == "Joint edit"


@isolate_apps()
def test_generic_statement_source_uses_its_link_owner_without_an_integrate_stream() -> None:
    class LocalLink(StatementLinkMixin, AngeeDataModel):
        sqid_prefix = "lnk_"
        name = models.CharField(max_length=80)

        class Meta(AngeeDataModel.Meta):
            abstract = False
            app_label = "base"

        def __str__(self) -> str:
            return self.name

    link = LocalLink(pk=7, name="Local source")
    reference = provenance_schema._statement_source_reference(SimpleNamespace(_readable_statement_link=link))
    assert reference is not None
    assert (reference.model, str(reference.id), reference.label) == ("base.LocalLink", link.public_id, "Local source")
    assert LocalLink.presentation_related == ()
