"""Focused contracts for generic extraction evidence and the local GLM adapter."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from PIL import Image

from angee.workflows_ocr.engines import DocumentPipelineError, DocumentSource, PageImage, PageResult
from angee.workflows_ocr.routing import acquire_native_parts
from angee.workflows_ocr.service import _merge, _validated_schema
from angee.workflows_ocr_glm.engine import GlmOllamaEngine
from tests.ocr_engines import FakeOcrEngine

SCHEMA = {
    "$id": "test.document.v1",
    "type": "object",
    "properties": {"number": {"type": "string"}},
    "required": ["number"],
    "additionalProperties": False,
}


def _page(source: int, page: int) -> PageImage:
    return PageImage(source, page, "image/jpeg", b"synthetic", 10, 10, 200)


def test_fake_engine_addresses_pages_by_source_and_page_without_collisions() -> None:
    engine = FakeOcrEngine()
    config = {"page_results": {"0:1": {"number": "first"}, "1:0": {"number": "second"}}}
    assert engine.extract_page(_page(0, 1), SCHEMA, model=None, config=config, timeout=1).value == {"number": "first"}
    assert engine.extract_page(_page(1, 0), SCHEMA, model=None, config=config, timeout=1).value == {"number": "second"}


def test_merge_preserves_repeated_rows_and_records_conflicting_claims() -> None:
    result, conflicts = _merge(
        [
            PageResult({"number": "A", "lines": [{"description": "same"}]}),
            PageResult({"number": "B", "lines": [{"description": "same"}]}),
        ]
    )
    assert result == {
        "number": "A",
        "lines": [{"description": "same"}, {"description": "same"}],
    }
    assert conflicts == {"number": ["A", "B"]}


def test_schema_owner_requires_object_root() -> None:
    assert _validated_schema(SCHEMA) == SCHEMA
    with pytest.raises(ValidationError, match="root must have type object"):
        _validated_schema({"$id": "bad", "type": "array"})


def test_glm_engine_rejects_nonlocal_provider_before_sending_page() -> None:
    model = SimpleNamespace(
        name="glm-ocr:latest",
        provider=SimpleNamespace(backend_class="ollama", base_url="https://example.invalid/v1"),
    )
    with pytest.raises(ValueError, match="loopback Ollama"):
        GlmOllamaEngine().extract_page(_page(0, 0), SCHEMA, model=model, config={}, timeout=1)


def test_native_acquisition_converts_input_errors_to_retained_pipeline_failures() -> None:
    source = DocumentSource(0, "a" * 64, "image/png", b"not an image")
    with pytest.raises(DocumentPipelineError, match=r"acquisition failed \(ValueError\)"):
        acquire_native_parts((source,))

    acquired = DocumentSource(0, "b" * 64, "text/plain", "retained", message_part=object())
    failed = DocumentSource(1, "a" * 64, "image/png", b"not an image")
    with pytest.raises(DocumentPipelineError) as error:
        acquire_native_parts((acquired, failed))
    assert [part.value for part in error.value.parts] == ["retained"]

    nul_text = DocumentSource(0, "c" * 64, "text/plain", b"invoice\x00text")
    with pytest.raises(DocumentPipelineError, match="acquisition failed"):
        acquire_native_parts((nul_text,))


@pytest.mark.parametrize("kind", ["text", "scan", "structured"])
def test_glm_document_pipeline_uses_native_evidence_before_model_mapping(kind, monkeypatch):
    if kind == "text":
        content, mime = b"Invoice DOC-1", "text/plain"
    elif kind == "scan":
        stream = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(stream, format="PNG")
        content, mime = stream.getvalue(), "image/png"
    else:
        content = (
            b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
            b'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
            b"<cbc:ID>DOC-1</cbc:ID></Invoice>"
        )
        mime = "application/xml"
    calls = []
    model, recognizer = object(), object()

    def generate(self, prompt, **kwargs):
        calls.append((prompt, kwargs))
        assert 0 < kwargs["timeout"] <= 10
        return ("Invoice DOC-1" if kwargs.get("page") else '{"number":"DOC-1"}'), {"duration_ms": 1}

    monkeypatch.setattr(GlmOllamaEngine, "_generate", generate)
    result = GlmOllamaEngine().extract_document(
        (DocumentSource(0, "a" * 64, mime, content),),
        SCHEMA,
        model=model,
        recognition_model=recognizer,
        config={},
        timeout=10,
    )
    assert result.value == {"number": "DOC-1"}
    assert len(calls) == (2 if kind == "scan" else 1)
    assert calls[-1][1]["model"] is model
    assert "DOC-1" in calls[-1][0]
    assert result.parts[0].kind == {"text": "native_text", "scan": "recognized_text", "structured": "structured"}[kind]
    if kind == "scan":
        assert calls[0][1]["model"] is recognizer
    else:
        assert not calls[0][1].get("page")


def test_glm_mapping_failure_retains_acquired_evidence_and_bounds_vendor_error(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private vendor response")

    monkeypatch.setattr(GlmOllamaEngine, "_generate", fail)
    with pytest.raises(DocumentPipelineError) as failure:
        GlmOllamaEngine().extract_document(
            (DocumentSource(0, "b" * 64, "text/plain", b"Invoice DOC-1"),),
            SCHEMA,
            model=object(),
            recognition_model=None,
            config={},
            timeout=10,
        )
    assert failure.value.parts[0].value == "Invoice DOC-1"
    assert "private" not in str(failure.value)
