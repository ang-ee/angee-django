"""Evidence resources preserve immutable writes and independent source access."""

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from angee.extraction import schema as evidence_schema
from angee.extraction.acquisition import PageCarrier
from tests.conftest import addon_schema, execute_schema, result_data
from tests.test_extraction_models import evidence as evidence
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
      extraction { id result record_model_label record_public_id
        sources { id file { id } }
        pages { source_page source { id } carrier_file { id } }
        parts { value source { id } }
      }
      extractionsource { id file { id } }
      extractionpage { source_page }
      extractionpart { value }
    }"""
    hidden = result_data(execute_schema(schema, query, user=reader))
    assert hidden == {"extraction": [], "extractionsource": [], "extractionpage": [], "extractionpart": []}
    row.with_actor(values["actor"]).grant_record_access("viewer", reader)
    assert not values["target"].with_actor(reader).has_access("read")
    visible = result_data(execute_schema(schema, query, user=reader))
    result = visible["extraction"][0]
    assert result["id"] == row.sqid and result["result"] == values["result"].value
    assert result["record_model_label"] == "storage.File"
    assert result["record_public_id"] == values["target"].sqid
    assert result["sources"][0]["file"] is None
    assert visible["extractionsource"][0]["file"] is None
    assert result["parts"] == []
    assert visible["extractionpart"] == []
    assert result["pages"][0]["source"]["id"] == result["sources"][0]["id"]
    assert visible["extractionpage"] == [{"source_page": 0}]
    values["target"].with_actor(values["actor"]).grant_record_access("viewer", reader)
    readable = result_data(execute_schema(schema, query, user=reader))
    assert readable["extraction"][0]["parts"][0]["value"] == values["result"].parts[0].value
    assert readable["extractionpart"] == [{"value": values["result"].parts[0].value}]
    detail = result_data(execute_schema(
        schema, "query($id: String!) { extraction_by_pk(id: $id) { id } }",
        {"id": row.sqid}, user=reader,
    ))
    assert detail == {"extraction_by_pk": {"id": row.sqid}}
