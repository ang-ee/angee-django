"""Evidence resources preserve immutable writes and independent source access."""

from dataclasses import replace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.extraction import schema as evidence_schema
from angee.extraction.acquisition import PageCarrier
from angee.extraction.contracts import DocumentPart, ExtractionPartKind, Source
from angee.iam.testing.graphql import measure_graphql_document
from angee.messaging.testing.models import Part
from tests.conftest import addon_schema, create_platform_admin, execute_schema, result_data
from tests.test_extraction_models import evidence as evidence
from tests.test_messaging_part_tree import part_tree as part_tree
from tests.test_storage import drive as drive


@pytest.fixture
def schema():
    return addon_schema(evidence_schema.schemas, "console")


def test_evidence_resources_expose_reads_without_generic_mutations(schema):
    resources = {resource.model_label for resource in schema.angee_resources}
    assert resources == {
        "extraction.Extraction", "extraction.ExtractionSource",
        "extraction.ExtractionPage", "extraction.ExtractionPart",
    }
    assert schema._schema.mutation_type is None
    for name in ("extraction", "extractionsource", "extractionpage", "extractionpart"):
        assert {name, f"{name}_by_pk", f"{name}_aggregate"} <= set(schema._schema.query_type.fields)


def test_extraction_part_values_are_batched_at_one_five_ten_twenty(schema, evidence):
    """Each admitted part reads its retained carrier even without carrier access."""

    retain, values = evidence
    part = values["result"].parts[0]
    result = replace(values["result"], parts=tuple(
        replace(part, metadata={"budget_index": index}) for index in range(20)
    ))
    row = retain(result=result)
    assert len(set(row.parts.values_list("carrier_file_id", flat=True))) == 20
    document = """query PartValues($limit: Int!) {
        extractionpart(limit: $limit, order_by: [{position: asc}]) { id value }
    }"""
    reads = {
        size: measure_graphql_document(schema, document, {"limit": size}, actor=values["actor"])
        for size in (1, 5, 10, 20)
    }
    for size, read in reads.items():
        assert len(read.data["extractionpart"]) == size
        assert [row["value"] for row in read.data["extractionpart"]] == [
            values["result"].parts[0].value
        ] * size
        assert read.statement_count == 5
        read.assert_no_audit_writes()
        read.assert_bytes(
            total=260_000, largest=180_000,
            cause="the composed policy pre-reads each referenced type (policy fan-out)",
        )
    for smaller, larger in ((1, 5), (5, 10), (10, 20)):
        reads[larger].assert_flat_from(reads[smaller])

    plain_pages = {
        "root": (
            "query($limit: Int!) { extractionpart(limit: $limit, order_by: [{position: asc}]) { id position } }", 3,
        ),
        "nested": ("query { extraction { parts { id position } } }", 7),
        "source": (
            "query($limit: Int!) { extractionpart(limit: $limit, order_by: [{position: asc}]) "
            "{ id source { id position } } }", 4,
        ),
    }
    for shape, (query, statements) in plain_pages.items():
        pages = {
            size: measure_graphql_document(
                schema, query, {"limit": size} if shape != "nested" else {}, actor=values["actor"],
            )
            for size in (1, 5, 10, 20)
        }
        assert {page.statement_count for page in pages.values()} == {statements}
        for smaller, larger in ((1, 5), (5, 10), (10, 20)):
            pages[larger].assert_flat_from(pages[smaller])

    reader = get_user_model().objects.create_user(username="part-values-reader")
    row.with_actor(values["actor"]).grant_record_access("viewer", reader)
    values["target"].with_actor(values["actor"]).grant_record_access("viewer", reader)
    carrier_id = row.parts.values_list("carrier_file_id", flat=True).first()
    carrier = type(values["target"])._base_manager.get(pk=carrier_id)
    assert not carrier.with_actor(reader).has_access("read")
    reader_reads = {
        size: measure_graphql_document(schema, document, {"limit": size}, actor=reader)
        for size in (1, 5, 10, 20)
    }
    for size, read in reader_reads.items():
        assert len(read.data["extractionpart"]) == size
        assert [part["value"] for part in read.data["extractionpart"]] == [
            values["result"].parts[0].value
        ] * size
        assert read.statement_count == 5
        read.assert_no_audit_writes()
    for smaller, larger in ((1, 5), (5, 10), (10, 20)):
        reader_reads[larger].assert_flat_from(reader_reads[smaller])


def test_nested_part_value_does_not_prepare_outer_part_page(schema, evidence):
    """A value selected on related parts does not prepare the root part page."""

    retain, values = evidence
    part = values["result"].parts[0]
    retain(result=replace(values["result"], parts=tuple(
        replace(part, metadata={"budget_index": index}) for index in range(20)
    )))
    document = """query NestedPartValue($limit: Int!) {
        extractionpart(limit: $limit, order_by: [{position: asc}]) {
            id extraction { id parts { id value } }
        }
    }"""
    reads = {
        size: measure_graphql_document(schema, document, {"limit": size}, actor=values["actor"])
        for size in (1, 5, 10, 20)
    }
    for size, read in reads.items():
        assert len(read.data["extractionpart"]) == size
        assert all(len(row["extraction"]["parts"]) == 20 for row in read.data["extractionpart"])
        assert read.statement_count == 9, (size, read.statement_count)
        read.assert_no_audit_writes()
    for smaller, larger in ((1, 5), (5, 10), (10, 20)):
        reads[larger].assert_flat_from(reads[smaller])


@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_message_part_reference_lists_are_flat_for_reader_and_admin(
    schema, evidence, part_tree, capture_graphql_document,
):
    retain, values = evidence
    message, parts = part_tree
    part = parts["plain"]
    source = Source(0, part.fragment.hash, "text/plain", part.fragment.text, message_part=part)
    result = replace(values["result"], parts=(DocumentPart(
        0, 0, "text/plain", ExtractionPartKind.NATIVE_TEXT, part.fragment.text, "native", part.fragment.hash,
    ),))
    admin = create_platform_admin("message-list-admin")
    reader = get_user_model().objects.create_user(username="message-list-reader")
    more_parts = [part]
    with system_context(reason="test.message_part_reference_list"):
        for position in range(1, 18):
            more_parts.append(Part.objects.create(
                message=message, position=position + 20, fragment=part.fragment,
            ))
    content_type = ContentType.objects.get_for_model(Part)
    ContentType.objects.get_for_id(content_type.pk)
    source_model = evidence_schema.ExtractionSource
    list_query = """query($ids: [String!]!) {
      extractionsource(where: {id: {_in: $ids}}, order_by: {position: asc}) { message_part_id }
    }"""
    nested_query = """query($id: String!) {
      extraction(where: {id: {_eq: $id}}) { sources { message_part_id } }
    }"""
    counts = {}
    for count in (3, 9, 18):
        sources = (source, *(
            Source(position, part.fragment.hash, "text/plain", part.fragment.text, message_part=more_parts[position])
            for position in range(1, count)
        ))
        extraction = retain(
            target=message, sources=sources, result=result, actor=admin, request_key=f"message-list-{count}",
        )
        write_relationships([RelationshipTuple(to_object_ref(extraction), "viewer", to_subject_ref(reader))])
        with system_context(reason="test.message_part_reference_list"):
            source_ids = [row.sqid for row in source_model.system_queryset().filter(extraction=extraction)]
        assert len(source_ids) == count
        for actor, readable in ((reader, False), (admin, True)):
            for shape, query, variables in (
                ("list", list_query, {"ids": source_ids}),
                ("nested", nested_query, {"id": extraction.sqid}),
            ):
                read = capture_graphql_document(schema, query, variables, actor=actor)
                rows = read.data["extractionsource"] if shape == "list" else read.data["extraction"][0]["sources"]
                assert len(rows) == count
                assert all((item["message_part_id"] is not None) == readable for item in rows)
                counts[(shape, readable, count)] = read.statement_count
    for shape in ("list", "nested"):
        for readable in (False, True):
            assert counts[(shape, readable, 3)] == counts[(shape, readable, 9)] == counts[(shape, readable, 18)]


@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_evidence_reader_cannot_expand_access_to_source_files(schema, evidence):
    retain, values = evidence
    row = retain(pages=(PageCarrier(source_position=0, page_position=0),))
    reader = get_user_model().objects.create_user(username="evidence-shared-reader")
    query = """{
      extraction { id display_name inference_configured result outcome document_map record_model_label record_public_id
        sources { id file { id } }
        pages { source_page provider_metadata source { id } carrier_file { id } }
        parts { value source { id } }
      }
      extractionsource { id file { id } }
      extractionpage { source_page provider_metadata }
      extractionpart { value }
    }"""
    hidden = result_data(execute_schema(schema, query, user=reader))
    assert hidden == {"extraction": [], "extractionsource": [], "extractionpage": [], "extractionpart": []}
    row.with_actor(values["actor"]).grant_record_access("viewer", reader)
    assert not values["target"].with_actor(reader).has_access("read")
    visible = result_data(execute_schema(schema, query, user=reader))
    result = visible["extraction"][0]
    assert result["id"] == row.sqid
    assert result["display_name"] == "extraction"
    assert result["inference_configured"] is False
    assert result["result"] is None and result["outcome"] is None and result["document_map"] is None
    assert result["record_model_label"] == "storage.File"
    assert result["record_public_id"] == values["target"].sqid
    assert result["sources"][0]["file"] is None
    assert visible["extractionsource"][0]["file"] is None
    assert result["parts"] == []
    assert visible["extractionpart"] == []
    assert visible["extractionpage"] == []
    assert result["pages"] == []
    values["target"].with_actor(values["actor"]).grant_record_access("viewer", reader)
    readable = result_data(execute_schema(schema, query, user=reader))
    assert readable["extraction"][0]["display_name"] == str(values["target"])
    assert readable["extraction"][0]["result"] == values["result"].value
    assert readable["extraction"][0]["outcome"]["claims"] == values["result"].claims
    assert readable["extraction"][0]["pages"][0]["provider_metadata"] == {}
    assert readable["extraction"][0]["parts"][0]["value"] == values["result"].parts[0].value
    assert readable["extractionpart"] == [{"value": values["result"].parts[0].value}]
    detail = result_data(execute_schema(
        schema, "query($id: String!) { extraction_by_pk(id: $id) { id } }",
        {"id": row.sqid}, user=reader,
    ))
    assert detail == {"extraction_by_pk": {"id": row.sqid}}


@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_message_part_identity_requires_current_part_read(schema, evidence, part_tree):
    retain, values = evidence
    message, parts = part_tree
    part = parts["plain"]
    source = Source(0, part.fragment.hash, "text/plain", part.fragment.text, message_part=part)
    result = replace(values["result"], parts=(DocumentPart(
        0, 0, "text/plain", ExtractionPartKind.NATIVE_TEXT, part.fragment.text, "native", part.fragment.hash,
    ),))
    admin = create_platform_admin("message-extraction-author")
    row = retain(target=message, sources=(source,), result=result, actor=admin, request_key="message-part")
    reader = get_user_model().objects.create_user(username="message-extraction-viewer")
    write_relationships([RelationshipTuple(to_object_ref(row), "viewer", to_subject_ref(reader))])
    query = "{ extractionsource { message_part_id } }"
    assert result_data(execute_schema(schema, query, user=reader)) == {
        "extractionsource": [{"message_part_id": None}],
    }
    assert result_data(execute_schema(schema, query, user=admin)) == {
        "extractionsource": [{"message_part_id": part.sqid}],
    }
