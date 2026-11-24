"""``ANGEE_REBAC_READ_THROUGH_PARENT``: children read with their admitted parent rows.

With the switch, base autoconfig turns on REBAC's ``REBAC_STRUCTURAL_PREFETCH``:
a relation field's children are read by the foreign key that names their admitted
parents, with no permission statement, where the policy proves that the parent's
action grants theirs. These tests exercise the relation-field contract on
test-owned models; the composed policy's edges are ``tests.native_read_through_parent``'s.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import strawberry_django
from django.conf import settings
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, override_settings
from rebac import RelationshipTuple, SubjectRef, system_context, to_object_ref, write_relationships
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.schema import parse_zed
from rebac.testing import install_schema
from strawberry import auto

from angee.base.models.base import AngeeDataModel
from angee.base.testing.queries import statement_startswith
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_many
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, result_data
from tests.conftest import SchemaAddon
from tests.tables import model_tables

# The switch's one effect, as base autoconfig derives it.
ON = {"ANGEE_REBAC_READ_THROUGH_PARENT": True, "REBAC_STRUCTURAL_PREFETCH": True}

POLICY = """
definition auth/user {}
definition angee/role {
    relation member: auth/user
}
definition tests/through_folder {
    relation reader: auth/user
    relation admin: angee/role // rebac:const=admin
    permission read = reader + admin->member
}
definition tests/through_note {
    relation folder: tests/through_folder // rebac:field=folder
    relation auditor: auth/user
    permission read = folder->read
    permission read__cost = auditor
}
definition tests/through_draft {
    relation folder: tests/through_folder // rebac:field=folder
    relation hidden: auth/user
    permission read = folder->read - hidden
}
"""


class ThroughFolder(AngeeDataModel):
    """A parent whose notes and drafts are read through it."""

    sqid_prefix = "tfd_"
    name = models.CharField(max_length=32)

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "tests/through_folder"


class ThroughNote(AngeeDataModel):
    """A child whose read is its folder's read; its ``cost`` is gated."""

    sqid_prefix = "tnt_"
    folder = models.ForeignKey(ThroughFolder, on_delete=models.CASCADE, related_name="notes")
    body = models.CharField(max_length=32)
    cost = models.IntegerField(default=0)

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "tests/through_note"
        ordering = ("body",)


class ThroughDraft(AngeeDataModel):
    """A child whose read excludes: holding the folder's read does not grant it."""

    sqid_prefix = "tdr_"
    folder = models.ForeignKey(ThroughFolder, on_delete=models.CASCADE, related_name="drafts")
    body = models.CharField(max_length=32)

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "tests/through_draft"
        ordering = ("body",)


@strawberry_django.type(ThroughNote)
class ThroughNoteType(AngeeNode):
    body: auto
    cost: int | None


@strawberry_django.type(ThroughDraft)
class ThroughDraftType(AngeeNode):
    body: auto


@strawberry_django.type(ThroughFolder)
class ThroughFolderType(AngeeNode):
    name: auto
    notes: list[ThroughNoteType] = actor_scoped_to_many("notes")
    drafts: list[ThroughDraftType] = actor_scoped_to_many("drafts")


@pytest.fixture
def through_case(db: None):
    """Alice reads two folders of three notes and three drafts, one draft hidden from her; a third folder is Bob's."""

    del db
    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    with model_tables((ThroughFolder, ThroughNote, ThroughDraft)):
        install_schema(parse_zed(POLICY), backend=active)
        try:
            alice = SubjectRef.of("auth/user", "alice")
            bob = SubjectRef.of("auth/user", "bob")
            folders = hasura_model_resource(
                ThroughFolderType, model=ThroughFolder, name="through_folders", filterable=["name"],
                sortable=["name"], aggregatable=[], insert=False, update=False, delete=False,
            )
            owner = GraphQLSchemas([
                SchemaAddon({
                    "public": {
                        "query": (folders.query,),
                        "types": (ThroughFolderType, ThroughNoteType, ThroughDraftType, *folders.types),
                    }
                })
            ])
            with system_context(reason="tests.read_through_parent.seed"):
                first = ThroughFolder.objects.create(name="First")
                second = ThroughFolder.objects.create(name="Second")
                third = ThroughFolder.objects.create(name="Third")
                for folder in (first, second, third):
                    # Created so that neither key order is their order: a read that loses it shows it.
                    for body in ("b", "c", "a"):
                        ThroughNote.objects.create(folder=folder, body=f"{folder.name}-{body}", cost=7)
                        ThroughDraft.objects.create(folder=folder, body=f"{folder.name}-{body}")
                withheld = ThroughDraft.objects.get(body="First-a")
            write_relationships([
                RelationshipTuple(to_object_ref(first), "reader", alice),
                RelationshipTuple(to_object_ref(second), "reader", alice),
                RelationshipTuple(to_object_ref(third), "reader", bob),
                RelationshipTuple(to_object_ref(withheld), "hidden", alice),
            ])
            yield SimpleNamespace(schema=owner.build("public"), alice=alice)
        finally:
            reset_backend()


FOLDERS = """
query {
  through_folders(order_by: [{name: asc}]) {
    name
    notes { body cost }
    drafts { body }
  }
}
"""


def _read(case: Any, *, actor: SubjectRef | None = None) -> tuple[dict[str, Any], list[str]]:
    """The document's data and statements, after one warm execution fills the process's policy caches."""

    user = actor or case.alice
    result_data(execute_schema(case.schema, FOLDERS, {}, user=user))
    with CaptureQueriesContext(connection) as captured:
        data = result_data(execute_schema(case.schema, FOLDERS, {}, user=user))
    return data, [query["sql"] for query in captured.captured_queries]


def _reads(statements: list[str], model: type[models.Model]) -> list[str]:
    """The statements that read ``model``'s rows, field-gate probes apart; a scoped one carries ``rebac_`` SQL."""

    return [
        sql
        for sql in statements
        if statement_startswith(sql, f'SELECT "{model._meta.db_table}".') and ' AS "pk", CASE WHEN ' not in sql
    ]


def _children(data: dict[str, Any], field: str) -> list[list[dict[str, Any]]]:
    return [folder[field] for folder in data["through_folders"]]


def test_the_switch_is_off_by_default() -> None:
    assert settings.ANGEE_REBAC_READ_THROUGH_PARENT is False
    assert settings.REBAC_STRUCTURAL_PREFETCH is False


def test_children_are_read_with_their_parents_in_order_and_keep_their_field_gates(through_case: Any) -> None:
    off, off_sql = _read(through_case)
    with override_settings(**ON):
        on, on_sql = _read(through_case)
    assert on == off
    assert [folder["name"] for folder in on["through_folders"]] == ["First", "Second"]
    assert _children(on, "notes") == [
        [{"body": f"{name}-{body}", "cost": None} for body in "abc"] for name in ("First", "Second")
    ]
    assert len(on_sql) <= len(off_sql)
    [scoped] = _reads(off_sql, ThroughNote)
    [delivered] = _reads(on_sql, ThroughNote)
    assert "rebac_" in scoped
    assert "rebac_" not in delivered


def test_children_whose_read_the_parent_does_not_grant_keep_their_statement(through_case: Any) -> None:
    off, off_sql = _read(through_case)
    with override_settings(**ON):
        on, on_sql = _read(through_case)
    assert _children(on, "drafts") == _children(off, "drafts") == [
        [{"body": "First-b"}, {"body": "First-c"}], [{"body": f"Second-{body}"} for body in "abc"],
    ]
    for statements in (off_sql, on_sql):
        [scoped] = _reads(statements, ThroughDraft)
        assert "rebac_" in scoped


def test_the_switch_acts_only_through_the_setting_it_derives(through_case: Any) -> None:
    """Set at run time without ``REBAC_STRUCTURAL_PREFETCH``, the switch changes no statement."""

    with override_settings(ANGEE_REBAC_READ_THROUGH_PARENT=True):
        data, statements = _read(through_case)
    assert [note["body"] for notes in _children(data, "notes") for note in notes] == [
        f"{name}-{body}" for name in ("First", "Second") for body in "abc"
    ]
    [scoped] = _reads(statements, ThroughNote)
    assert "rebac_" in scoped
