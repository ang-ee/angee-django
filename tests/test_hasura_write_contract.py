"""Native Hasura write envelopes carry composed input extensions."""

from __future__ import annotations

from typing import Any

import strawberry
import strawberry_django
from strawberry.types import get_object_definition

from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import execute_schema, make_addon, result_data
from tests.scopedemo.models import SharedDoc


@strawberry_django.type(SharedDoc)
class WriteContractDocType(AngeeNode):
    """Use an existing model without persisting the captured envelope."""

    title: strawberry.auto


@strawberry.input
class WriteContractDetailInput:
    """Nested donor values retain omission and explicit null."""

    value: str
    note: str | None = strawberry.UNSET


class CaptureBackend:
    """Observe the native write boundary without a second persistence path."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, dict[str, Any]]] = []

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> SharedDoc:
        self.calls.append(("create", None, data))
        return SharedDoc(title=data["title"])

    def update(self, info: strawberry.Info, pk: str, data: dict[str, Any]) -> SharedDoc:
        self.calls.append(("update", pk, data))
        return SharedDoc(title=data["title"])


def test_input_donors_reach_native_create_and_update_envelopes() -> None:
    """Composed input extensions preserve nested values, nulls, and omission."""

    backend = CaptureBackend()
    resource = hasura_model_resource(
        WriteContractDocType, model=SharedDoc, name="write_contract_docs",
        filterable=["id", "title"], sortable=["title"], aggregatable=["id"],
        writable=["title"], write_backend=backend, delete=False,
    )

    @strawberry.input(name=get_object_definition(resource.insert_input_type, strict=True).name, extend=True)
    class InsertExtras:
        details: list[WriteContractDetailInput] | None = strawberry.UNSET
        note: str | None = strawberry.UNSET

    @strawberry.input(name=get_object_definition(resource.set_input_type, strict=True).name, extend=True)
    class UpdateExtras:
        details: list[WriteContractDetailInput] | None = strawberry.UNSET
        note: str | None = strawberry.UNSET

    schema = GraphQLSchemas([make_addon(schemas={"public": {
        "query": [resource.query], "mutation": [resource.mutation],
        "types": [WriteContractDocType, *resource.types], "input_extensions": (InsertExtras, UpdateExtras),
    }})]).build("public")
    result_data(execute_schema(schema, """mutation {
      insert_write_contract_docs_one(object: {
        title: "Inserted", details: [{value: "First"}, {value: "Second", note: null}]
      }) { title }
      update_write_contract_docs_by_pk(pk_columns: {id: "public-key"}, _set: {
        title: "Updated", note: null
      }) { title }
    }"""))

    assert backend.calls == [
        ("create", None, {"title": "Inserted", "details": [{"value": "First"}, {"value": "Second", "note": None}]}),
        ("update", "public-key", {"title": "Updated", "note": None}),
    ]
