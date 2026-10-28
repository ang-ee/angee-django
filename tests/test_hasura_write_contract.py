"""Native Hasura write envelopes carry composed input extensions to their model."""

from __future__ import annotations

from typing import Any, cast

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test.utils import isolate_apps
from graphql import GraphQLInputObjectType, get_named_type
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from strawberry import auto
from strawberry.types import get_object_definition
from strawberry_django.optimizer import DjangoOptimizerExtension

from angee.base.models import AngeeDataModel
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, HasuraLines, hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import create_platform_admin, create_user, execute_schema, make_addon, result_data
from tests.linesdemo.models import Document, DocumentLine, Tag
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
        detail: WriteContractDetailInput | None = strawberry.UNSET
        note: str | None = strawberry.UNSET

    schemas = GraphQLSchemas([make_addon(schemas={"public": {
        "query": [resource.query], "mutation": [resource.mutation],
        "types": [WriteContractDocType, *resource.types], "input_extensions": (InsertExtras, UpdateExtras),
    }})])
    schema = schemas.build("public")
    # This backend captures inputs without persisting a row. Native response
    # projection is exercised by persisted-backend tests, not this envelope test.
    with DjangoOptimizerExtension.disabled():
        result_data(execute_schema(schema, """mutation {
          insert_write_contract_docs_one(object: {
            title: "Inserted", details: [{value: "First"}, {value: "Second", note: null}]
          }) { title }
          update_write_contract_docs_by_pk(pk_columns: {id: "public-key"}, _set: {
            title: "Updated", note: null, detail: {value: "Pinned"}
          }) { title }
        }"""))

    assert backend.calls == [
        ("create", None, {"title": "Inserted", "details": [{"value": "First"}, {"value": "Second", "note": None}]}),
        ("update", "public-key", {"title": "Updated", "note": None, "detail": {"value": "Pinned"}}),
    ]

    metadata = next(item for item in schemas.resources("public") if item.model is SharedDoc)
    detail = next(field for field in metadata.fields if field.name == "detail")
    assert (detail.kind, detail.scalar, detail.readable, detail.creatable, detail.updatable) == (
        "object", None, False, False, True,
    )


@strawberry_django.type(Tag)
class StampTagType(AngeeNode):
    """A recorded stamp, read as a model-backed node."""

    name: auto


@strawberry_django.type(DocumentLine)
class StampedLineType(AngeeNode):
    """One line of a stamped document."""

    label: auto
    position: auto


@pytest.mark.parametrize("filterable", [False, True])
@isolate_apps()
def test_write_relation_declaration_supplies_public_id_inputs(filterable: bool) -> None:
    """A relation's write owner also declares its public scalar, even without a filter."""

    class Referenced(AngeeDataModel):
        key = models.IntegerField(primary_key=True)

        class Meta:
            app_label = "write_contract"

    class Reference(AngeeDataModel):
        product = models.ForeignKey(Referenced, on_delete=models.CASCADE)

        class Meta:
            app_label = "write_contract"

    @strawberry_django.type(Reference)
    class ReferenceType(AngeeNode):
        pass

    resource = hasura_model_resource(
        ReferenceType, model=Reference, name="declared_relations",
        filterable=["id", *(["product"] if filterable else [])],
        sortable=["id"], aggregatable=["id"],
        insertable=["product"], updatable=["product"],
        write_backend=AngeeHasuraWriteBackend(Reference, public_id_fields=("product",)),
    )
    schema = strawberry.Schema(query=resource.query, mutation=resource.mutation, types=resource.types)
    for input_type in (resource.insert_input_type, resource.set_input_type):
        definition = get_object_definition(input_type, strict=True)
        graphql_type = schema._schema.get_type(definition.name)
        assert isinstance(graphql_type, GraphQLInputObjectType)
        assert get_named_type(graphql_type.fields["product"].type).name == "ID"


@strawberry_django.type(Document)
class StampedDocumentType(AngeeNode):
    """A document whose writes carry the test-only ``stamp`` input extension."""

    title: auto

    @strawberry_django.field
    def lines(self) -> list[StampedLineType]:
        return list(self.lines.order_by("position", "pk"))


@strawberry_django.type(Document, name="StampedDocumentType", extend=True)
class StampedDocumentStamps:
    """A computed model list another addon contributes onto the document node."""

    @strawberry_django.field
    def stamps(self) -> list[StampTagType]:
        return list(Tag.objects.filter(name__startswith="document:", name__endswith=f"@{self.pk}"))


_STAMPED = hasura_model_resource(
    StampedDocumentType, model=Document, name="stamped_documents",
    filterable=["id", "title"], sortable=["title"], aggregatable=["id"], writable=["title"],
    lines=HasuraLines(field="lines", model=DocumentLine, node=StampedLineType, writable=("label", "position")),
    id_column="sqid",
)


@strawberry.input(name=get_object_definition(_STAMPED.insert_input_type, strict=True).name, extend=True)
class StampedDocumentInsertExtras:
    """Insert-only donor values; ``orphan`` has no consuming model owner."""

    stamp: str | None = strawberry.UNSET
    stamps: list[strawberry.ID] | None = strawberry.UNSET
    orphan: str | None = strawberry.UNSET


@strawberry.input(name=get_object_definition(_STAMPED.set_input_type, strict=True).name, extend=True)
class StampedDocumentSetExtras:
    """Patch donor values for the document."""

    stamp: str | None = strawberry.UNSET
    stamps: list[strawberry.ID] | None = strawberry.UNSET


@strawberry.input(name=get_object_definition(_STAMPED.nested_input_types["lines"], strict=True).name, extend=True)
class StampedLineExtras:
    """Donor values for one nested document line."""

    stamp: str | None = strawberry.UNSET


_STAMPED_SCHEMAS = GraphQLSchemas([make_addon(schemas={"public": {
    "query": [_STAMPED.query], "mutation": [_STAMPED.mutation],
    "types": [StampedDocumentType, StampedLineType, StampTagType, *_STAMPED.types],
    "type_extensions": [StampedDocumentStamps],
    "input_extensions": [StampedDocumentInsertExtras, StampedDocumentSetExtras, StampedLineExtras],
}})])
# Build once at import, while the throwaway addon's manifest is current; later
# lookups reuse this cached build.
_STAMPED_SCHEMA = _STAMPED_SCHEMAS.build("public")


def _owned_document(title: str) -> tuple[Document, Any]:
    """Seed one document and the user holding its write-granting owner relation."""

    owner = create_user(f"owner-{title.lower()}")
    with system_context(reason="test.input_extensions.seed"):
        document = Document.objects.create(title=title)
    write_relationships([
        RelationshipTuple(resource=to_object_ref(document), relation="manager", subject=to_subject_ref(owner)),
    ])
    return document, owner


def _stamps() -> list[str]:
    with system_context(reason="test.input_extensions.read"):
        return sorted(Tag.objects.values_list("name", flat=True))


def _titles() -> list[str]:
    with system_context(reason="test.input_extensions.read"):
        return sorted(Document.objects.values_list("title", flat=True))


def test_insert_applies_input_extensions_after_the_row_write(composed_tables: None) -> None:
    """The model hook receives values no model field owns, once the row exists."""

    result = result_data(execute_schema(_STAMPED_SCHEMA, """mutation {
      insert_stamped_documents_one(object: {title: "Stamped", stamp: "first"}) { id title }
    }""", user=create_platform_admin("author")))

    with system_context(reason="test.input_extensions.read"):
        document = Document.objects.get(title="Stamped")
    assert result["insert_stamped_documents_one"] == {"id": str(document.sqid), "title": "Stamped"}
    assert _stamps() == [f"document:first@{document.pk}"]


def test_update_applies_input_extensions_after_the_row_write(composed_tables: None) -> None:
    """A patch writes its model fields, then hands its extension values to the hook."""

    document, owner = _owned_document("Draft")

    result_data(execute_schema(_STAMPED_SCHEMA, f"""mutation {{
      update_stamped_documents_by_pk(
        pk_columns: {{id: "{document.sqid}"}}, _set: {{title: "Renamed", stamp: "second"}}
      ) {{ title }}
    }}""", user=owner))

    assert _titles() == ["Renamed"]
    assert _stamps() == [f"document:second@{document.pk}"]


def test_save_applies_parent_and_line_input_extensions(composed_tables: None) -> None:
    """A document save applies an extension-only patch and each child line's values."""

    document, owner = _owned_document("Draft")

    result_data(execute_schema(_STAMPED_SCHEMA, f"""mutation {{
      stamped_documents_save(pk: "{document.sqid}", patch: {{stamp: "patch"}}, lines: [
        {{label: "Line", position: 0, stamp: "line"}}
      ]) {{ lines {{ label }} }}
    }}""", user=owner))

    with system_context(reason="test.input_extensions.read"):
        line = DocumentLine.objects.get(document=document)
    assert _stamps() == [f"document:patch@{document.pk}", f"documentline:line@{line.pk}"]


@pytest.mark.parametrize("operation", ["insert", "update"])
def test_a_raising_input_extension_rolls_back_the_row_write(composed_tables: None, operation: str) -> None:
    """The hook shares the row write's transaction, so its failure leaves no write behind."""

    document, owner = _owned_document("Kept")
    write = (
        'insert_stamped_documents_one(object: {title: "Rejected", stamp: "reject"}) { id }'
        if operation == "insert"
        else f'update_stamped_documents_by_pk(pk_columns: {{id: "{document.sqid}"}}, '
        '_set: {title: "Rejected", stamp: "reject"}) { id }'
    )

    result = execute_schema(_STAMPED_SCHEMA, f"mutation {{ {write} }}", user=owner)

    assert result.errors is not None
    assert _titles() == ["Kept"]
    assert _stamps() == []


def test_an_unconsumed_input_extension_value_fails_fast_without_a_row(composed_tables: None) -> None:
    """A value that reaches the terminal hook names itself and rolls the insert back."""

    author = create_user("author")
    with actor_context(author), pytest.raises(
        ImproperlyConfigured, match="linesdemo.Document has no owner for input extension values: orphan",
    ):
        AngeeHasuraWriteBackend(Document).create(cast(Any, None), {"title": "Orphaned", "orphan": "value"})

    result = execute_schema(_STAMPED_SCHEMA, """mutation {
      insert_stamped_documents_one(object: {title: "Orphaned", orphan: "value"}) { id }
    }""", user=author)

    assert result.errors is not None
    assert _titles() == []


def test_input_extension_fields_are_writable_in_resource_metadata() -> None:
    """Every field of the final insert/set input is creatable/updatable, extensions included."""

    metadata = next(item for item in _STAMPED_SCHEMAS.resources("public") if item.model is Document)
    fields = {field.name: field for field in metadata.fields}

    assert metadata.create_fields == ("title", "stamp", "stamps", "orphan")
    assert metadata.update_fields == ("title", "stamp", "stamps")
    assert (fields["stamp"].readable, fields["stamp"].creatable, fields["stamp"].updatable) == (False, True, True)
    assert (fields["orphan"].creatable, fields["orphan"].updatable) == (True, False)
    stamps = fields["stamps"]
    assert (stamps.kind, stamps.relation_model_label) == ("list", "linesdemo.Tag")
    assert (stamps.readable, stamps.creatable, stamps.updatable) == (True, True, True)
    assert metadata.lines is not None
    line_stamp = next(field for field in metadata.lines.fields if field.name == "stamp")
    assert (line_stamp.creatable, line_stamp.updatable) == (True, True)


def test_optional_claim_adapter_preserves_custom_untracked_backend_replay(composed_tables: None) -> None:
    """Idempotency wraps a native custom backend even when its model has no history."""

    class PersistingBackend(CaptureBackend):
        def create(self, info: strawberry.Info, data: dict[str, Any]) -> SharedDoc:
            self.calls.append(("create", None, data))
            return SharedDoc.objects.create(title=data["title"], is_shared=True)

    backend = PersistingBackend()
    resource = hasura_model_resource(
        WriteContractDocType, model=SharedDoc, name="claimed_custom_docs",
        filterable=["id", "title"], sortable=["title"], aggregatable=["id"],
        insertable=["title"], update=False, delete=False, write_backend=backend,
    )
    schema = GraphQLSchemas([make_addon(schemas={"public": {
        "query": [resource.query], "mutation": [resource.mutation],
        "types": [WriteContractDocType, *resource.types],
    }})]).build("public")
    author = create_user("custom-claim-author")
    query = """mutation($title:String!) {
      insert_claimed_custom_docs_one(object:{title:$title}, idempotency_key:"custom-request") {id title}
    }"""
    first = result_data(execute_schema(schema, query, {"title": "Original"}, user=author))
    assert result_data(execute_schema(schema, query, {"title": "Original"}, user=author)) == first
    conflict = execute_schema(schema, query, {"title": "Changed"}, user=author)
    assert conflict.errors is not None
    assert conflict.errors[0].extensions == {"code": "CREATION_KEY_CONFLICT"}
    assert backend.calls == [("create", None, {"title": "Original"})]
    with system_context(reason="test.claim.revoke_read"):
        SharedDoc.objects.filter(title="Original").update(is_shared=False)
    denied = execute_schema(schema, query, {"title": "Original"}, user=author)
    assert denied.errors is not None
    assert denied.errors[0].extensions == {"code": "PERMISSION_DENIED"}
    assert len(backend.calls) == 1


def test_claimed_create_rolls_back_failed_input_extensions_before_retry(composed_tables: None) -> None:
    """A failed insert consumes neither its key nor its row or input-extension effects."""

    author = create_platform_admin("retryable-claim-author")
    query = """mutation($stamp:String!) {
      insert_stamped_documents_one(object:{title:"Retryable", stamp:$stamp}, idempotency_key:"retryable") {id title}
    }"""
    refused = execute_schema(_STAMPED_SCHEMA, query, {"stamp": "reject"}, user=author)
    assert refused.errors is not None
    assert _titles() == []
    assert _stamps() == []
    first = result_data(execute_schema(_STAMPED_SCHEMA, query, {"stamp": "accepted"}, user=author))
    assert result_data(execute_schema(_STAMPED_SCHEMA, query, {"stamp": "accepted"}, user=author)) == first
    assert _titles() == ["Retryable"]
    assert len(_stamps()) == 1
