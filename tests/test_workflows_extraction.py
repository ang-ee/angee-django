"""Focused contracts for generic extraction evidence and inference adapters."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from pydantic import ValidationError as PydanticValidationError
from pydantic_ai.messages import BinaryContent, ModelResponse, ToolCallPart

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows.attempts import RecoveryMode
from angee.workflows.steps import DecisionApplyStep, TransientStepError
from angee.workflows_extraction import service
from angee.workflows_extraction.engines import (
    DocumentPart,
    DocumentPipelineError,
    DocumentSource,
    InferenceMappingEngine,
    MappingResult,
    PageImage,
)
from angee.workflows_extraction.routing import acquire_native_parts
from angee.workflows_extraction.service import _validated_schema
from angee.workflows_extraction.steps import (
    CollectCarriersStepImpl,
    PreparePagesStepImpl,
    ProcessEvidenceStepImpl,
    RecognizePageStepImpl,
    ReviseEvidenceStepImpl,
)
from tests.conftest import SchemaAddon
from tests.extraction_models import Extraction as _Extraction  # noqa: F401 - registers composed test models.
from tests.test_agents import InferenceModel as _InferenceModel  # noqa: F401 - registers composed test models.

SCHEMA = {
    "$id": "test.document.v1",
    "type": "object",
    "properties": {"number": {"type": "string"}},
    "required": ["number"],
    "additionalProperties": False,
}


def test_revise_evidence_uses_the_single_decision_apply_factory() -> None:
    assert issubclass(ReviseEvidenceStepImpl, DecisionApplyStep)
    assert "run" not in ReviseEvidenceStepImpl.__dict__
    assert ReviseEvidenceStepImpl.resolution_path == ("review", "resolutions", 0)
    assert ReviseEvidenceStepImpl.recovery_capability(attempt=object()).mode is RecoveryMode.FRESH


def test_extraction_evidence_uses_native_closed_kind_enums() -> None:
    from angee.workflows_extraction import schema as extraction_schema

    parts = {
        key: tuple(extraction_schema.schemas["console"].get(key, ()))
        for key in SCHEMA_PART_KEYS
    }
    rendered = GraphQLSchemas([SchemaAddon({"console": parts})]).build("console").as_str()

    assert "enum ExtractionPartKind" in rendered
    assert "enum RetiredIdentityKind" in rendered
    assert "kind: RetiredIdentityKind!" in rendered


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


@pytest.mark.parametrize(
    "step_impl",
    [
        PreparePagesStepImpl,
        RecognizePageStepImpl,
        CollectCarriersStepImpl,
        ProcessEvidenceStepImpl,
    ],
)
def test_extraction_execution_policy_is_input_bound(step_impl: type) -> None:
    """Every reusable extraction activity admits policy through input, not definition config."""

    schema = step_impl.input_contract().raw_schema

    assert step_impl.config_model is None
    assert step_impl.config_form_spec() is None
    assert schema is not None
    assert schema["properties"]["engine_config"]["widget"] == "json"
    if step_impl in (PreparePagesStepImpl, CollectCarriersStepImpl):
        assert "schema" not in schema["properties"]
        assert "engine" not in schema["properties"]
    elif step_impl is RecognizePageStepImpl:
        assert "schema" not in schema["properties"]
        assert "engine" in schema["properties"]
    else:
        assert {"schema", "engine"} <= schema["properties"].keys()


@pytest.mark.parametrize(
    ("step_impl", "step_input"),
    [
        (
            PreparePagesStepImpl,
            {
                "files": [],
                "message_parts": [],
                "model": None,
                "recognition_model": None,
                "target_model": "tests.target",
                "target_id": "target-1",
            },
        ),
        (CollectCarriersStepImpl, {"prepared": {}, "recognition": {}}),
        (
            ProcessEvidenceStepImpl,
            {
                "prepared": {},
                "recognition_results": [],
                "hold_reasons": [],
                "completed_page_count": 0,
            },
        ),
    ],
)
def test_extraction_steps_admit_per_invocation_policy(
    step_impl: type,
    step_input: dict[str, object],
) -> None:
    policy: dict[str, object] = {
        "engine_config": {"recognition_config": {"max_tokens": 512}},
    }
    if step_impl is ProcessEvidenceStepImpl:
        policy.update({
            "schema": {"$id": "tests.extraction.v1", "type": "object"},
            "engine": "document_profile",
        })

    value = step_impl.validate_input({**step_input, **policy})

    assert value.engine_config == policy["engine_config"]
    if step_impl is ProcessEvidenceStepImpl:
        assert value.schema_ == policy["schema"]
        assert value.engine == "document_profile"


def test_recognize_page_input_owns_inference_defaults_and_validates_timeout() -> None:
    value = RecognizePageStepImpl.validate_input({
        "source_position": 0,
        "page_position": 1,
        "image_file_id": "fil_image",
        "image_digest": "a" * 64,
        "width": 100,
        "height": 200,
        "dpi": 300,
        "model_id": "imd_recognition",
        "config_digest": "b" * 64,
        "engine_config": {"max_tokens": 512},
    })

    assert value.engine == "inference"
    assert value.timeout == 60
    assert value.engine_config == {"max_tokens": 512}
    with pytest.raises(PydanticValidationError, match="greater than 0"):
        RecognizePageStepImpl.validate_input({
            **value.model_dump(mode="json"),
            "timeout": 0,
        })


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

    monkeypatch.setattr(service.settings, "ANGEE_EXTRACTION_MAX_BYTES", 4)
    with pytest.raises(ValidationError, match="configured byte limit"):
        service._document_sources((), (_message_part(text="five!"),))


def test_message_part_source_requires_actor_read_access() -> None:
    actor = object()
    denied = _message_part()
    denied.with_actor = lambda admitted: denied if admitted is actor else None
    denied.has_access = lambda _permission: False
    target = SimpleNamespace()
    target.with_actor = lambda admitted: target if admitted is actor else None
    target.has_access = lambda _permission: True

    with pytest.raises(PermissionDenied, match="every extraction source"):
        service._authorize((), (denied,), target, actor=actor)


def test_schema_owner_requires_object_root() -> None:
    assert _validated_schema(SCHEMA) == SCHEMA
    with pytest.raises(ValidationError, match="root must have type object"):
        _validated_schema({"$id": "bad", "type": "array"})


def test_inference_mapping_uses_catalogue_model_without_provider_restriction() -> None:
    response = SimpleNamespace(
        text='{"number":"INV-42"}',
        provider_response_id="response-1",
    )
    requested = {}

    def infer(messages, *, output_schema, settings):
        requested.update(messages=messages, output_schema=output_schema, settings=settings)
        return response, {"input_tokens": 23, "output_tokens": 7, "tokens": 30, "requests": 1}

    model = SimpleNamespace(
        status="available",
        model_use="chat",
        provider=SimpleNamespace(
            backend_class="anthropic",
            backend=SimpleNamespace(),
        ),
        infer=infer,
    )
    part = DocumentPart(0, None, "text/plain", "native_text", "Invoice INV-42", "native", "hash")

    result = InferenceMappingEngine().map_text_parts(
        (part,),
        SCHEMA,
        model=model,
        config={"max_tokens": 128, "thinking": False},
        timeout=5,
    )

    assert result.value == {"number": "INV-42"}
    assert result.claims["/number"][0]["part_position"] == 0
    assert result.engine_metadata["usage"] == {
        "input_tokens": 23,
        "output_tokens": 7,
        "tokens": 30,
        "requests": 1,
    }
    assert result.usage_delta == result.engine_metadata["usage"]
    assert requested["settings"] == {
        "timeout": 5,
        "max_tokens": 128,
        "temperature": 0,
        "thinking": False,
    }
    assert requested["output_schema"] == SCHEMA


def test_inference_mapping_invalid_json_retains_bounded_response_diagnostics() -> None:
    raw_output = "invoice data, but not JSON"
    response = SimpleNamespace(
        text=raw_output,
        provider_response_id="response-invalid",
        finish_reason="length",
    )
    model = SimpleNamespace(
        status="available",
        model_use="chat",
        infer=lambda *args, **kwargs: (
            response,
            {"input_tokens": 31, "output_tokens": 9, "tokens": 40, "requests": 1},
        ),
    )
    part = DocumentPart(0, None, "text/plain", "native_text", "Invoice INV-42", "native", "hash")

    with pytest.raises(DocumentPipelineError) as raised:
        InferenceMappingEngine().map_text_parts((part,), SCHEMA, model=model, config={}, timeout=5)

    metadata = raised.value.metadata
    assert metadata == {
        "duration_ms": metadata["duration_ms"],
        "usage": {
            "input_tokens": 31,
            "output_tokens": 9,
            "tokens": 40,
            "requests": 1,
        },
        "provider_response_id": "response-invalid",
        "finish_reason": "length",
        "output_text_length": len(raw_output),
        "output_text_sha256": hashlib.sha256(raw_output.encode()).hexdigest(),
    }
    assert raised.value.usage_delta == metadata["usage"]
    assert raw_output not in str(metadata)


def test_inference_mapping_consumes_native_structured_tool_result() -> None:
    response = ModelResponse(parts=[ToolCallPart("inference_output", {"number": "INV-43"}, "call-1")])
    model = SimpleNamespace(
        status="available",
        model_use="chat",
        infer=lambda *args, **kwargs: (
            response,
            {"input_tokens": 19, "output_tokens": 5, "tokens": 24, "requests": 1},
        ),
    )

    result = InferenceMappingEngine().map_text_parts(
        (DocumentPart(0, None, "text/plain", "native_text", "Invoice INV-43", "native", "hash"),),
        SCHEMA,
        model=model,
        config={},
        timeout=5,
    )

    assert result.value == {"number": "INV-43"}
    assert isinstance(result, MappingResult)


def test_inference_model_roles_and_retired_status_share_the_execution_validator():
    model = SimpleNamespace(
        status="available",
        model_use="chat",
    )
    engine = InferenceMappingEngine()
    engine.validate_model(model, role="mapping")
    with pytest.raises(ValueError, match="image-capable"):
        engine.validate_model(model, role="recognition")
    model.model_use = "multimodal"
    engine.validate_model(model, role="recognition")
    model.status = "retired"
    with pytest.raises(ValueError, match="available"):
        engine.validate_model(model, role="mapping")


def test_inference_recognition_carries_native_image_and_zero_temperature() -> None:
    requested: dict[str, object] = {}
    response = SimpleNamespace(
        text="Invoice INV-44",
        provider_response_id="recognition-1",
        finish_reason="stop",
    )

    def infer(messages, *, images, settings):
        requested.update(messages=messages, images=images, settings=settings)
        return response, {"input_tokens": 11, "output_tokens": 4, "tokens": 15, "requests": 1}

    model = SimpleNamespace(status="available", model_use="multimodal", infer=infer)

    result = InferenceMappingEngine().recognize_page(
        _page(0, 0),
        model=model,
        config={"max_tokens": 256},
        timeout=5,
    )

    assert result.text == "Invoice INV-44"
    assert requested["settings"] == {"timeout": 5, "max_tokens": 256, "temperature": 0}
    images = requested["images"]
    assert isinstance(images, tuple)
    assert len(images) == 1 and isinstance(images[0], BinaryContent)
    assert images[0].data == b"synthetic"
    assert result.engine_metadata["usage"] == {
        "input_tokens": 11,
        "output_tokens": 4,
        "tokens": 15,
        "requests": 1,
    }
    assert result.usage_delta == result.engine_metadata["usage"]


def test_inference_recognition_invalid_response_exposes_usage_delta() -> None:
    usage = {"input_tokens": 7, "output_tokens": 1, "tokens": 8, "requests": 1}
    model = SimpleNamespace(
        status="available",
        model_use="multimodal",
        infer=lambda *args, **kwargs: (
            SimpleNamespace(text=None, provider_response_id="recognition-invalid"),
            usage,
        ),
    )

    with pytest.raises(DocumentPipelineError) as raised:
        InferenceMappingEngine().recognize_page(
            _page(0, 0),
            model=model,
            config={},
            timeout=5,
        )

    assert raised.value.stage == "recognition_response"
    assert raised.value.usage_delta == usage


@pytest.mark.parametrize("operation", ["mapping", "recognition"])
def test_inference_engine_preserves_retryable_provider_failures(operation: str) -> None:
    """Provider 429s reach the workflow retry seam instead of becoming terminal pipeline errors."""

    class RateLimitedError(RuntimeError):
        status_code = 429

    def infer(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RateLimitedError("provider throttled")

    model = SimpleNamespace(
        status="available",
        model_use="multimodal" if operation == "recognition" else "chat",
        infer=infer,
    )
    engine = InferenceMappingEngine()

    with pytest.raises(TransientStepError, match="provider throttled"):
        if operation == "recognition":
            engine.recognize_page(_page(0, 0), model=model, config={}, timeout=5)
        else:
            engine.map_text_parts(
                (DocumentPart(0, None, "text/plain", "native_text", "Invoice", "native", "hash"),),
                SCHEMA,
                model=model,
                config={},
                timeout=5,
            )


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


def test_inference_mapping_failure_retains_acquired_evidence_and_bounds_vendor_error():
    def fail(*args, **kwargs):
        raise RuntimeError("private vendor response")

    model = SimpleNamespace(status="available", model_use="chat", infer=fail)
    with pytest.raises(DocumentPipelineError) as failure:
        InferenceMappingEngine().map_text_parts(
            (DocumentPart(0, None, "text/plain", "native_text", "Invoice DOC-1", "native", "b" * 64),),
            SCHEMA,
            model=model,
            config={},
            timeout=10,
        )
    assert failure.value.parts[0].value == "Invoice DOC-1"
    assert "private" not in str(failure.value)
