"""A scalar foreign-key public id projects the row's stored key, never its target."""

from __future__ import annotations

from typing import Any

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import reset_backend
from rebac.schema import parse_zed
from rebac.testing import install_schema

from angee.graphql.ids import PublicID, public_id_field, require_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.testing.documents import execute_schema, result_data
from tests.conftest import addon_schema, create_user
from tests.core_persistence import OwnedRow, OwnershipContainer

# The container is the reference target. No definition here lets the reader read it:
# the projection discloses the row's key whatever the reader may see of the target.
GATED_KEY_SCHEMA = """
definition auth/user {}
definition scopedemo/owned_row {
    relation owner: auth/user // rebac:field=owner
    relation container_reader: auth/user
    permission read = owner
    permission transfer = owner
    permission write__owner = transfer
    permission read__container = container_reader
}
"""


@strawberry_django.type(OwnedRow)
class OwnedRowKeys(AngeeNode):
    container: PublicID | None = public_id_field("container", nullable=True)
    required_container = public_id_field("container")


@strawberry.type
class OwnedRowKeysQuery:
    @strawberry_django.field
    def owned_rows(self) -> list[OwnedRowKeys]:
        return OwnedRow.objects.order_by("pk")


ROWS = "{ owned_rows { container required_container redacted_fields } }"


@pytest.fixture
def gated_key(db: None) -> Any:
    install_schema(parse_zed(GATED_KEY_SCHEMA))
    try:
        yield addon_schema({"public": {"query": [OwnedRowKeysQuery]}}, "public")
    finally:
        reset_backend()


def _rows(owner: Any, count: int, *, readable_key: bool = True) -> list[OwnedRow]:
    with system_context(reason="tests.graphql_ids.rows"):
        rows = [
            OwnedRow.objects.create(owner=owner, container=OwnershipContainer.objects.create())
            for _ in range(count)
        ]
    if readable_key:
        write_relationships([
            RelationshipTuple(to_object_ref(row), "container_reader", to_subject_ref(owner)) for row in rows
        ])
    return rows


def _key(row: OwnedRow) -> str:
    return str(require_public_id(OwnershipContainer, row.container_id))


def test_projection_reads_the_stored_key_and_never_queries_the_target(gated_key: Any) -> None:
    owner = create_user("public-id-owner")
    target_table = OwnershipContainer._meta.db_table
    statements: list[int] = []
    rows: list[OwnedRow] = []
    for count in (1, 24):
        rows += _rows(owner, count)
        with CaptureQueriesContext(connection) as captured:
            data = result_data(execute_schema(gated_key, ROWS, user=owner))
        assert data["owned_rows"] == [
            {"container": _key(row), "required_container": _key(row), "redacted_fields": []} for row in rows
        ]
        assert not [query["sql"] for query in captured if target_table in query["sql"]]
        statements.append(len(captured))
    assert statements[0] == statements[1]


def test_nullable_projection_reads_an_unset_key_as_null_and_the_required_one_refuses_it(gated_key: Any) -> None:
    owner = create_user("public-id-unset")
    with system_context(reason="tests.graphql_ids.unset"):
        OwnedRow.objects.create(owner=owner)

    sdl = gated_key.as_str()
    assert "  container: ID\n" in sdl
    assert "  required_container: ID!\n" in sdl
    nullable = result_data(execute_schema(gated_key, "{ owned_rows { container } }", user=owner))
    assert nullable == {"owned_rows": [{"container": None}]}
    required = execute_schema(gated_key, "{ owned_rows { required_container } }", user=owner)
    assert required.data is None
    assert [error.path for error in required.errors] == [["owned_rows", 0, "required_container"]]


def test_field_gate_on_the_key_redacts_the_projection(gated_key: Any) -> None:
    owner = create_user("public-id-gated")
    readable = _rows(owner, 1)[0]
    _rows(owner, 1, readable_key=False)

    data = result_data(execute_schema(gated_key, "{ owned_rows { container redacted_fields } }", user=owner))
    assert data["owned_rows"] == [
        {"container": _key(readable), "redacted_fields": []},
        {"container": None, "redacted_fields": ["container"]},
    ]


@isolate_apps("tests")
@pytest.mark.parametrize(
    ("field_name", "annotation", "nullable", "refusal"),
    [
        ("by_code", PublicID | None, True, r"tests\.Row\.by_code must target its model's primary key"),
        ("title", PublicID | None, True, r"tests\.Row\.title must be a forward foreign key"),
        ("target", PublicID, True, r"tests\.Row\.target: the 'key' annotation disagrees with .*nullable=True"),
        ("target", PublicID | None, False, r"tests\.Row\.target: the 'key' annotation disagrees with .*nullable=False"),
    ],
)
def test_schema_build_refuses_a_misdeclared_projection(
    field_name: str, annotation: Any, nullable: bool, refusal: str,
) -> None:
    class Target(models.Model):
        code = models.CharField(max_length=40, unique=True)

        class Meta:
            app_label = "tests"

    class Row(models.Model):
        target = models.ForeignKey(Target, null=True, on_delete=models.CASCADE, related_name="+")
        by_code = models.ForeignKey(Target, to_field="code", on_delete=models.CASCADE, related_name="+")
        title = models.CharField(max_length=40)

        class Meta:
            app_label = "tests"

    RowKey = strawberry_django.type(Row)(type("RowKey", (), {
        "__annotations__": {"key": annotation}, "key": public_id_field(field_name, nullable=nullable),
    }))

    # graphql-core reports a field-thunk failure as a TypeError caused by the refusal.
    with pytest.raises(TypeError, match=refusal) as caught:
        strawberry.Schema(query=OwnedRowKeysQuery, types=[RowKey])
    assert isinstance(caught.value.__cause__, ImproperlyConfigured)
