"""Evidence resources preserve immutable writes and independent source access."""

from dataclasses import replace

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

from angee.extraction import schema as evidence_schema
from angee.extraction.acquisition import PageCarrier
from angee.extraction.contracts import DocumentPart, DocumentSource, ExtractionPartKind
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
    source = DocumentSource(0, part.fragment.hash, "text/plain", part.fragment.text, message_part=part)
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
