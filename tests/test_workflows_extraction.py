"""Focused contracts for generic extraction evidence and the local GLM adapter."""

from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from PIL import Image
from pydantic_ai.messages import ModelResponse, ToolCallPart

from angee.workflows_extraction import service
from angee.workflows_extraction.engines import (
    DocumentPart,
    DocumentPipelineError,
    DocumentSource,
    InferenceMappingEngine,
    PageImage,
    PageResult,
)
from angee.workflows_extraction.routing import acquire_native_parts
from angee.workflows_extraction.service import _validated_schema
from angee.workflows_extraction.steps import PreparePagesStepImpl, RecognizePageStepImpl
from angee.workflows_extraction_glm.engine import GlmOllamaEngine
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


def _message_part(
    *, role: str = "body", mime_type: str = "text/plain", text: str = "retained message evidence",
) -> SimpleNamespace:
    return SimpleNamespace(
        pk=1,
        sqid="prt_retained",
        fragment_id=1,
        fragment=SimpleNamespace(text=text, hash=hashlib.sha256(text.encode()).hexdigest()),
        role=role,
        type=mime_type,
        has_access=lambda _permission: True,
    )


@pytest.mark.parametrize("step_impl", [PreparePagesStepImpl, RecognizePageStepImpl])
def test_extraction_step_engine_config_is_authored_as_json(step_impl: type) -> None:
    """Provider/profile options remain editable without weakening structured config projection."""

    spec = step_impl.config_form_spec()

    assert spec is not None
    assert spec["properties"]["engine_config"] == {
        "type": "object",
        "widget": "json",
        "label": "Engine Config",
        "omittable": True,
    }
    assert step_impl.normalize_config({
        **({"schema": {}, "engine": "profile"} if step_impl is PreparePagesStepImpl else {}),
        "engine_config": {"nested": {"enabled": False}, "limit": 0, "nullable": None},
    })["engine_config"] == {
        "nested": {"enabled": False}, "limit": 0, "nullable": None,
    }


def test_recognize_page_config_retains_authored_defaults() -> None:
    spec = RecognizePageStepImpl.config_form_spec()

    assert spec is not None
    assert spec["properties"]["timeout"] == {
        "type": "integer",
        "minimum": 1,
        "label": "Timeout",
        "description": "Provider timeout in whole seconds.",
        "defaultValue": 60,
        "omittable": True,
    }
    assert RecognizePageStepImpl.config_defaults() == {"engine": "glm", "timeout": 60}
    with pytest.raises(ValidationError, match="config.timeout"):
        RecognizePageStepImpl.normalize_config({"timeout": 0})


@pytest.mark.parametrize("role", ["body", "title", "quoted", "signature", "header"])
def test_retained_textual_message_roles_are_authorized_source_evidence(role: str) -> None:
    part = _message_part(role=role)

    source = service._document_sources((), (part,))[0]

    assert source.source_position == 0
    assert source.message_part is part
    assert source.content == part.fragment.text
    assert source.content_hash == part.fragment.hash
    assert service._source_fact(source) == {
        "position": 0,
        "message_part": "prt_retained",
        "content_hash": part.fragment.hash,
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"role": "attachment"}, "unsupported textual role"),
        ({"type": "application/json"}, "Only textual message parts"),
        ({"fragment_id": None}, "require a retained fragment"),
    ],
)
def test_message_part_sources_reject_unsupported_contracts(change: dict[str, object], message: str) -> None:
    part = _message_part()
    for field, value in change.items():
        setattr(part, field, value)

    with pytest.raises(ValidationError, match=message):
        service._document_sources((), (part,))


def test_message_part_sources_reject_tampered_hashes_and_byte_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered = _message_part()
    tampered.fragment.hash = "0" * 64
    with pytest.raises(ValidationError, match="no longer matches"):
        service._document_sources((), (tampered,))

    monkeypatch.setattr(service.settings, "ANGEE_OCR_MAX_BYTES", 4)
    with pytest.raises(ValidationError, match="configured byte limit"):
        service._document_sources((), (_message_part(text="five!"),))


def test_message_part_source_requires_actor_read_access() -> None:
    denied = _message_part()
    denied.has_access = lambda _permission: False

    with pytest.raises(PermissionDenied, match="every extraction source"):
        service._authorize((), (denied,), SimpleNamespace(has_access=lambda _permission: True))


def test_fake_engine_addresses_pages_by_source_and_page_without_collisions() -> None:
    engine = FakeOcrEngine()
    config = {"page_results": {"0:1": {"number": "first"}, "1:0": {"number": "second"}}}
    assert engine.extract_page(_page(0, 1), SCHEMA, model=None, config=config, timeout=1).value == {"number": "first"}
    assert engine.extract_page(_page(1, 0), SCHEMA, model=None, config=config, timeout=1).value == {"number": "second"}


def test_schema_owner_requires_object_root() -> None:
    assert _validated_schema(SCHEMA) == SCHEMA
    with pytest.raises(ValidationError, match="root must have type object"):
        _validated_schema({"$id": "bad", "type": "array"})


def test_inference_mapping_uses_catalogue_model_without_provider_restriction() -> None:
    response = SimpleNamespace(
        text='{"number":"INV-42"}',
        usage=SimpleNamespace(input_tokens=23, output_tokens=7),
        provider_response_id="response-1",
    )
    requested = {}

    def chat(messages, *, model_settings, model_request_parameters):
        requested.update(settings=model_settings, parameters=model_request_parameters)
        return response

    model = SimpleNamespace(
        status="available",
        model_use="chat",
        provider=SimpleNamespace(
            backend_class="anthropic",
            backend=SimpleNamespace(),
        ),
        chat=chat,
    )
    part = DocumentPart(0, None, "text/plain", "native_text", "Invoice INV-42", "native", "hash")

    value, claims, metadata = InferenceMappingEngine().map_text_parts(
        (part,), SCHEMA, model=model, config={"max_tokens": 128}, timeout=5,
    )

    assert value == {"number": "INV-42"}
    assert claims["/number"][0]["part_position"] == 0
    assert metadata["input_tokens"] == 23
    assert metadata["output_tokens"] == 7
    assert requested["parameters"].output_mode == "auto"
    assert requested["parameters"].output_object.json_schema == SCHEMA


def test_inference_mapping_invalid_json_retains_bounded_response_diagnostics() -> None:
    raw_output = "invoice data, but not JSON"
    response = SimpleNamespace(
        text=raw_output,
        usage=SimpleNamespace(input_tokens=31, output_tokens=9),
        provider_response_id="response-invalid",
        finish_reason="length",
    )
    model = SimpleNamespace(
        status="available",
        model_use="chat",
        chat=lambda *args, **kwargs: response,
    )
    part = DocumentPart(0, None, "text/plain", "native_text", "Invoice INV-42", "native", "hash")

    with pytest.raises(DocumentPipelineError) as raised:
        InferenceMappingEngine().map_text_parts((part,), SCHEMA, model=model, config={}, timeout=5)

    metadata = raised.value.metadata
    assert metadata == {
        "duration_ms": metadata["duration_ms"],
        "input_tokens": 31,
        "output_tokens": 9,
        "provider_response_id": "response-invalid",
        "finish_reason": "length",
        "output_text_length": len(raw_output),
        "output_text_sha256": hashlib.sha256(raw_output.encode()).hexdigest(),
    }
    assert raw_output not in str(metadata)


def test_inference_mapping_consumes_native_structured_tool_result() -> None:
    response = ModelResponse(
        parts=[ToolCallPart("document_extraction", {"number": "INV-43"}, "call-1")]
    )
    response.usage.input_tokens = 19
    response.usage.output_tokens = 5
    model = SimpleNamespace(
        status="available",
        model_use="chat",
        chat=lambda *args, **kwargs: response,
    )

    value, _claims, _metadata = InferenceMappingEngine().map_text_parts(
        (DocumentPart(0, None, "text/plain", "native_text", "Invoice INV-43", "native", "hash"),),
        SCHEMA,
        model=model,
        config={},
        timeout=5,
    )

    assert value == {"number": "INV-43"}


def test_glm_engine_rejects_nonlocal_provider_before_sending_page() -> None:
    model = SimpleNamespace(
        name="glm-ocr:latest",
        provider=SimpleNamespace(backend_class="ollama", base_url="https://example.invalid/v1"),
    )
    with pytest.raises(ValueError, match="loopback Ollama"):
        GlmOllamaEngine().extract_page(_page(0, 0), SCHEMA, model=model, config={}, timeout=1)


@pytest.mark.parametrize("base_url", [
    "https://example.invalid/v1", "ftp://localhost/v1", "http://user:pass@localhost/v1",
    "http://localhost/v1?token=secret", "http://localhost/v1#fragment",
])
def test_glm_configuration_and_execution_reject_the_same_provider(base_url, monkeypatch):
    model = SimpleNamespace(
        status="available", model_use="multimodal", provider_model_name="test-model",
        provider=SimpleNamespace(backend_class="ollama", base_url=base_url),
    )
    def unexpected_request(*args, **kwargs):
        raise AssertionError("An invalid provider must never receive document evidence.")
    monkeypatch.setattr("httpx.Client", unexpected_request)
    engine = GlmOllamaEngine()
    with pytest.raises(ValueError, match="loopback Ollama"):
        engine.validate_model(model, role="recognition")
    with pytest.raises(ValueError, match="loopback Ollama"):
        engine.recognize_page(_page(0, 0), model=model, config={}, timeout=1)


def test_glm_model_roles_and_retired_status_share_the_execution_validator():
    model = SimpleNamespace(
        status="available", model_use="chat",
        provider=SimpleNamespace(backend_class="ollama", base_url="http://localhost:11434/v1"),
    )
    engine = GlmOllamaEngine()
    engine.validate_model(model, role="mapping")
    with pytest.raises(ValueError, match="image-capable"):
        engine.validate_model(model, role="recognition")
    model.model_use = "multimodal"
    engine.validate_model(model, role="recognition")
    model.status = "retired"
    with pytest.raises(ValueError, match="available"):
        engine.validate_model(model, role="mapping")


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

    with pytest.raises(DocumentPipelineError) as unsupported:
        acquire_native_parts((DocumentSource(0, "d" * 64, "application/octet-stream", b"not-an-image"),))
    assert (unsupported.value.stage, unsupported.value.code) == ("acquisition", "unsupported_media_type")

    with pytest.raises(DocumentPipelineError) as empty:
        acquire_native_parts((DocumentSource(0, "e" * 64, "application/octet-stream", b""),))
    assert (empty.value.stage, empty.value.code) == ("acquisition", "empty_source")


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
