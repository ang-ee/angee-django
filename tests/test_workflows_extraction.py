"""Focused contracts for generic extraction evidence and inference adapters."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from pydantic import ValidationError as PydanticValidationError
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart

from angee.agents.models import InferenceModelUse, InferenceResult
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows.steps import TransientStepError
from angee.workflows_agents.inference import InferenceCallError
from angee.workflows_extraction import inference as extraction_inference
from angee.workflows_extraction import service
from angee.workflows_extraction import steps as extraction_steps
from angee.workflows_extraction.contracts import (
    DocumentPart,
    DocumentPipelineError,
    DocumentSource,
    MappingResult,
    PageImage,
)
from angee.workflows_extraction.enums import ExtractionErrorCode, ExtractionRole
from angee.workflows_extraction.inference import (
    map_text_parts,
    recognize_page,
)
from angee.workflows_extraction.profiles import UnconfiguredExtractionProfile
from angee.workflows_extraction.routing import acquire_native_parts
from angee.workflows_extraction.service import _validated_schema
from angee.workflows_extraction.steps import (
    CollectCarriersStepImpl,
    PreparePagesStepImpl,
    ProcessEvidenceStepImpl,
    RecognizePageStepImpl,
)
from tests.conftest import SchemaAddon
from tests.extraction_models import Extraction
from tests.test_agents import InferenceModel as _InferenceModel  # noqa: F401 - registers composed test models.

SCHEMA = {
    "$id": "test.document.v1",
    "type": "object",
    "properties": {"number": {"type": "string"}},
    "required": ["number"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    ("status", "provenance", "error_code", "expected"),
    [
        (
            "failed",
            {"document": {"failure": {"stage": "inference", "code": "invalid_response"}}},
            "inference:invalid_response",
            True,
        ),
        ("failed", {"document": {"failure": {"stage": "inference"}}}, "recognition:failed", True),
        ("failed", {"document": {"failure": {"stage": "recognition"}}}, "inference:failed", False),
        ("succeeded", {"document": {"failure": {"stage": "inference"}}}, "inference:failed", False),
        ("failed", {}, "inference:failed", False),
        ("failed", {"document": {}}, "inference:failed", False),
        ("failed", {"document": {"failure": {"code": "failed"}}}, "inference:failed", False),
        ("failed", {"document": {"failure": None}}, "inference:failed", False),
    ],
)
def test_extraction_failed_at_inference_uses_retained_stage(
    status: str,
    provenance: dict[str, object],
    error_code: str,
    expected: bool,
) -> None:
    extraction = Extraction(status=status, provenance=provenance, error_code=error_code)

    assert extraction.failed_at_inference is expected


def test_extraction_evidence_uses_native_closed_kind_enums() -> None:
    from angee.workflows_extraction import schema as extraction_schema

    parts = {key: tuple(extraction_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}
    rendered = GraphQLSchemas([SchemaAddon({"console": parts})]).build("console").as_str()

    assert "enum ExtractionPartKind" in rendered
    assert "enum RetiredIdentityKind" in rendered
    assert "kind: RetiredIdentityKind!" in rendered


def _page(source: int, page: int) -> PageImage:
    return PageImage(source, page, "image/jpeg", b"synthetic", 10, 10, 200)


def _message_part(
    *,
    role: str = "body",
    mime_type: str = "text/plain",
    text: str = "retained message evidence",
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
    assert schema["properties"]["profile_config"]["widget"] == "json"
    if step_impl is PreparePagesStepImpl:
        assert "schema" not in schema["properties"]
        assert "profile" in schema["properties"]
    elif step_impl in (CollectCarriersStepImpl, RecognizePageStepImpl):
        assert "schema" not in schema["properties"]
        assert "profile" not in schema["properties"]
    else:
        assert {"schema", "profile"} <= schema["properties"].keys()


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
        "profile_config": {"recognition_config": {"max_tokens": 512}},
    }
    if step_impl is ProcessEvidenceStepImpl:
        policy.update(
            {
                "schema": {"$id": "tests.extraction.v1", "type": "object"},
                "profile": "document_profile",
            }
        )

    value = step_impl.validate_input({**step_input, **policy})

    assert value.profile_config == policy["profile_config"]
    if step_impl is ProcessEvidenceStepImpl:
        assert value.schema_ == policy["schema"]
        assert value.profile == "document_profile"


def test_recognize_page_input_owns_inference_defaults_and_validates_timeout() -> None:
    value = RecognizePageStepImpl.validate_input(
        {
            "source_position": 0,
            "page_position": 1,
            "image_file_id": "fil_image",
            "image_digest": "a" * 64,
            "width": 100,
            "height": 200,
            "dpi": 300,
            "model_id": "imd_recognition",
            "config_digest": "b" * 64,
            "profile_config": {"max_tokens": 512},
        }
    )

    assert "profile" not in type(value).model_fields
    assert value.timeout == 60
    assert value.profile_config == {"max_tokens": 512}
    with pytest.raises(PydanticValidationError, match="greater than 0"):
        RecognizePageStepImpl.validate_input(
            {
                **value.model_dump(mode="json"),
                "timeout": 0,
            }
        )


def test_recognize_page_uses_retained_actor_subject_for_file_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    image_bytes = b"synthetic page"
    image_digest = hashlib.sha256(image_bytes).hexdigest()
    actor = object()
    actor_subject = object()
    owner_subjects: list[object] = []
    image_file = MagicMock(
        sqid="fil_image",
        upload_state="ready",
        content_hash=image_digest,
        drive=SimpleNamespace(sqid="drv_test"),
    )
    image_file.with_actor.return_value.has_access.return_value = True
    image_file.open_stream.return_value = BytesIO(image_bytes)
    model = MagicMock(sqid="imd_recognition")
    model.with_actor.return_value.has_access.return_value = True
    text_file = SimpleNamespace(sqid="fil_text")
    file_model = MagicMock()
    file_manager = file_model.objects
    file_manager.select_related.return_value.get.return_value = image_file
    file_manager.ingest_stream.return_value = text_file
    model_model = MagicMock()
    model_model.objects.get.return_value = model
    run = SimpleNamespace(
        admission_actor=lambda **kwargs: actor,
        admission_actor_subject=lambda **kwargs: actor_subject,
        debit_budget=lambda usage: None,
    )
    value = SimpleNamespace(
        source_position=0,
        page_position=0,
        image_file_id="fil_image",
        image_digest=image_digest,
        width=100,
        height=200,
        dpi=300,
        model_id="imd_recognition",
        config_digest="digest",
        profile_config={},
        timeout=60,
    )
    recognize = MagicMock()
    recognize.return_value = SimpleNamespace(
        text="recognized text",
        usage_delta=None,
        duration_ms=1,
    )
    monkeypatch.setattr(
        extraction_steps,
        "external_operation_request",
        lambda *args, **kwargs: SimpleNamespace(request_key="recognition-request", input={}),
    )
    monkeypatch.setattr(RecognizePageStepImpl, "validate_input", staticmethod(lambda request: value))
    monkeypatch.setattr(extraction_steps, "canonical_json_sha256", lambda config: "digest")
    monkeypatch.setattr(extraction_steps, "actor_context", lambda value: nullcontext())
    monkeypatch.setattr(
        extraction_steps.apps,
        "get_model",
        lambda app, name: file_model if (app, name) == ("storage", "File") else model_model,
    )
    monkeypatch.setattr(extraction_steps, "recognize_page", recognize)
    monkeypatch.setattr(
        extraction_steps,
        "actor_user_id",
        lambda value: owner_subjects.append(value) or 7,
    )

    result = RecognizePageStepImpl()._recognize(SimpleNamespace(run=run))

    assert result.outcome == "recognized"
    assert owner_subjects == [actor_subject]
    assert file_manager.ingest_stream.call_args.kwargs["owner_id"] == 7


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


def test_inference_mapping_uses_shared_request_and_parsed_output(monkeypatch: pytest.MonkeyPatch) -> None:
    response = ModelResponse(parts=[TextPart("provider output")], provider_response_id="response-1")
    usage = {"input_tokens": 23, "output_tokens": 7, "tokens": 30, "requests": 1}
    call = MagicMock(return_value=InferenceResult(response, usage, {"number": "DOC-42"}))
    monkeypatch.setattr(extraction_inference, "call_inference", call)
    part = DocumentPart(0, None, "text/plain", "native_text", "Document DOC-42", "native", "hash")
    step, model = object(), object()

    result = map_text_parts(
        (part,),
        SCHEMA,
        step_run=step,
        model=model,
        config={"max_tokens": 128, "thinking": False},
        timeout=5,
    )

    assert isinstance(result, MappingResult)
    assert result.value == {"number": "DOC-42"}
    assert result.claims["/number"][0]["part_position"] == 0
    assert result.provider_metadata["usage"] == usage == result.usage_delta
    args, kwargs = call.call_args
    assert args[:2] == (step, model)
    assert kwargs == {
        "role": "mapping", "uses": ExtractionRole.MAPPING.accepted_model_uses,
    }
    assert args[2].settings == {"timeout": 5, "max_tokens": 128, "temperature": 0, "thinking": False}
    assert args[2].output_schema == SCHEMA


def test_inference_mapping_invalid_output_retains_bounded_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_output = "document data, but not JSON"
    response = ModelResponse(
        parts=[TextPart(raw_output)], provider_response_id="response-invalid", finish_reason="length"
    )
    usage = {"input_tokens": 31, "output_tokens": 9, "tokens": 40, "requests": 1}
    call = MagicMock(side_effect=InferenceCallError(ValueError("Invalid output"), response=response, usage=usage))
    monkeypatch.setattr(extraction_inference, "call_inference", call)
    part = DocumentPart(0, None, "text/plain", "native_text", "Document DOC-42", "native", "hash")

    with pytest.raises(DocumentPipelineError) as raised:
        map_text_parts((part,), SCHEMA, step_run=object(), model=object(), config={}, timeout=5)

    metadata = raised.value.metadata
    assert raised.value.stage == "mapping_response"
    assert metadata == {
        "duration_ms": metadata["duration_ms"],
        "usage": usage,
        "provider_response_id": "response-invalid",
        "finish_reason": "length",
        "output_text_length": len(raw_output),
        "output_text_sha256": hashlib.sha256(raw_output.encode()).hexdigest(),
    }
    assert raised.value.usage_delta == usage
    assert raw_output not in str(metadata)


def test_inference_recognition_carries_native_image_and_zero_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    response = ModelResponse(parts=[TextPart("Document DOC-44")], provider_response_id="recognition-1")
    usage = {"input_tokens": 11, "output_tokens": 4, "tokens": 15, "requests": 1}
    call = MagicMock(return_value=InferenceResult(response, usage))
    monkeypatch.setattr(extraction_inference, "call_inference", call)

    result = recognize_page(
        _page(0, 0), step_run=object(), model=object(), config={"max_tokens": 256}, timeout=5
    )

    request = call.call_args.args[2]
    assert result.text == "Document DOC-44"
    assert request.settings == {"timeout": 5, "max_tokens": 256, "temperature": 0}
    assert len(request.images) == 1 and isinstance(request.images[0], BinaryContent)
    assert request.images[0].data == b"synthetic"
    assert call.call_args.kwargs == {
        "role": "recognition", "uses": ExtractionRole.RECOGNITION.accepted_model_uses,
    }
    assert result.provider_metadata["usage"] == usage == result.usage_delta


def test_inference_recognition_invalid_response_exposes_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = {"input_tokens": 7, "output_tokens": 1, "tokens": 8, "requests": 1}
    monkeypatch.setattr(
        extraction_inference, "call_inference", MagicMock(return_value=InferenceResult(ModelResponse(parts=[]), usage))
    )
    with pytest.raises(DocumentPipelineError) as raised:
        recognize_page(_page(0, 0), step_run=object(), model=object(), config={}, timeout=5)
    assert raised.value.stage == "recognition_response"
    assert raised.value.usage_delta == usage


@pytest.mark.parametrize("operation", ["mapping", "recognition"])
def test_inference_preserves_shared_retry_classification(operation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        extraction_inference, "call_inference", MagicMock(side_effect=TransientStepError("provider throttled"))
    )
    with pytest.raises(TransientStepError, match="provider throttled"):
        if operation == "recognition":
            recognize_page(_page(0, 0), step_run=object(), model=object(), config={}, timeout=5)
        else:
            map_text_parts((), SCHEMA, step_run=object(), model=object(), config={}, timeout=5)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), "invalid"])
@pytest.mark.parametrize("operation", ["mapping", "recognition"])
def test_invalid_extraction_timeout_never_enters_provider_retry(
    operation: str, timeout: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = MagicMock()
    monkeypatch.setattr(extraction_inference, "call_inference", call)
    with pytest.raises(DocumentPipelineError) as failure:
        if operation == "recognition":
            recognize_page(_page(0, 0), step_run=object(), model=object(), config={}, timeout=timeout)
        else:
            map_text_parts((), SCHEMA, step_run=object(), model=object(), config={}, timeout=timeout)
    call.assert_not_called()
    assert failure.value.code == "invalid_config"
    assert failure.value.stage == f"{operation}_request"


def test_unconfigured_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="Select a document extraction profile"):
        UnconfiguredExtractionProfile().process_parts((), (), SCHEMA, config={})


@pytest.mark.parametrize("operation", ["mapping", "recognition"])
@pytest.mark.parametrize(
    "config", [{"max_tokens": "invalid"}, {"temperature": "invalid"}, {"max_tokens": 0}, {"max_tokens": float("inf")}],
)
def test_invalid_published_inference_config_routes_to_manual_review(
    operation: str, config: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = MagicMock()
    monkeypatch.setattr(extraction_inference, "call_inference", call)
    with pytest.raises(DocumentPipelineError) as failure:
        if operation == "recognition":
            recognize_page(_page(0, 0), step_run=object(), model=object(), config=config, timeout=5)
        else:
            map_text_parts((), SCHEMA, step_run=object(), model=object(), config=config, timeout=5)
    assert failure.value.stage == f"{operation}_request"
    assert failure.value.code == "invalid_config"
    call.assert_not_called()


def test_missing_decoded_mapping_retains_paid_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = {"tokens": 4, "requests": 1}
    monkeypatch.setattr(
        extraction_inference, "call_inference",
        MagicMock(return_value=InferenceResult(ModelResponse(parts=[]), usage)),
    )
    with pytest.raises(DocumentPipelineError) as failure:
        map_text_parts((), SCHEMA, step_run=object(), model=object(), config={}, timeout=5)
    assert failure.value.stage == "mapping_response"
    assert failure.value.code == "invalid_response"
    assert failure.value.usage_delta == usage
    assert failure.value.metadata["usage"] == usage


@pytest.mark.parametrize(
    "status,code,expected",
    [
        ("failed", ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED, True),
        ("succeeded", ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED, False),
        ("failed", "other", False),
    ],
)
def test_correspondence_hold_owned_by_extraction(status: str, code: str, expected: bool) -> None:
    assert Extraction(status=status, error_code=code).awaiting_correspondence is expected


def test_native_acquisition_converts_input_errors_to_retained_pipeline_failures() -> None:
    profile = UnconfiguredExtractionProfile()
    source = DocumentSource(0, "a" * 64, "image/png", b"not an image")
    with pytest.raises(DocumentPipelineError, match=r"acquisition failed \(ValueError\)"):
        acquire_native_parts((source,), profile=profile)

    acquired = DocumentSource(0, "b" * 64, "text/plain", "retained", message_part=object())
    failed = DocumentSource(1, "a" * 64, "image/png", b"not an image")
    with pytest.raises(DocumentPipelineError) as error:
        acquire_native_parts((acquired, failed), profile=profile)
    assert [part.value for part in error.value.parts] == ["retained"]

    nul_text = DocumentSource(0, "c" * 64, "text/plain", b"document\x00text")
    with pytest.raises(DocumentPipelineError, match="acquisition failed"):
        acquire_native_parts((nul_text,), profile=profile)

    with pytest.raises(DocumentPipelineError) as unsupported:
        acquire_native_parts(
            (DocumentSource(0, "d" * 64, "application/octet-stream", b"not-an-image"),), profile=profile,
        )
    assert (unsupported.value.stage, unsupported.value.code) == ("acquisition", "unsupported_media_type")

    with pytest.raises(DocumentPipelineError) as empty:
        acquire_native_parts((DocumentSource(0, "e" * 64, "application/octet-stream", b""),), profile=profile)
    assert (empty.value.stage, empty.value.code) == ("acquisition", "empty_source")


def test_inference_mapping_failure_retains_acquired_evidence_and_bounds_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        extraction_inference,
        "call_inference",
        MagicMock(
            side_effect=InferenceCallError(
                RuntimeError("private provider response"),
                response=None,
                usage={},
            )
        ),
    )
    with pytest.raises(DocumentPipelineError) as failure:
        map_text_parts(
            (DocumentPart(0, None, "text/plain", "native_text", "Document DOC-1", "native", "b" * 64),),
            SCHEMA,
            step_run=object(),
            model=object(),
            config={},
            timeout=10,
        )
    assert failure.value.parts[0].value == "Document DOC-1"
    assert "private" not in str(failure.value)


@pytest.mark.parametrize(
    "operation,model_use,compatible",
    [
        ("mapping", InferenceModelUse.CHAT, True),
        ("mapping", InferenceModelUse.MULTIMODAL, True),
        ("mapping", InferenceModelUse.IMAGE, False),
        ("recognition", InferenceModelUse.CHAT, False),
        ("recognition", InferenceModelUse.MULTIMODAL, True),
        ("recognition", InferenceModelUse.IMAGE, True),
    ],
)
@pytest.mark.parametrize("readable", [False, True])
def test_extraction_inference_authorizes_model_once_before_provider(
    operation: str,
    model_use: InferenceModelUse,
    compatible: bool,
    readable: bool,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    from angee.agents.backends import InferenceBackend
    from angee.workflows_agents import inference as workflow_inference

    monkeypatch.setattr(workflow_inference, "system_context", lambda **kwargs: nullcontext())

    actor = object()
    model = _InferenceModel(name="test", model_use=model_use, status="available")
    read = MagicMock(return_value=SimpleNamespace(has_access=MagicMock(return_value=readable)))
    monkeypatch.setattr(model, "with_actor", read)
    identity = {"provider": "provider", "backend": "test", "endpoint": "local", "model": "test"}
    monkeypatch.setattr(model, "deployment_identity", lambda **kwargs: identity)
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {operation: [identity]}
    response = ModelResponse(parts=[TextPart("Document DOC-42")])
    usage = {"requests": 1, "tokens": 3}
    provider_call = MagicMock(return_value=InferenceResult(response, usage, {"number": "DOC-42"}))
    monkeypatch.setattr(model, "infer", provider_call)
    run = SimpleNamespace(admission_actor=lambda **kwargs: actor, debit_budget=MagicMock())
    provider = SimpleNamespace(backend=InferenceBackend(SimpleNamespace()))
    model._state.fields_cache["provider"] = provider
    step = SimpleNamespace(run=run)

    def invoke():
        if operation == "recognition":
            return recognize_page(_page(0, 0), step_run=step, model=model, config={}, timeout=5)
        return map_text_parts((), SCHEMA, step_run=step, model=model, config={}, timeout=5)

    if readable and compatible:
        invoke()
        provider_call.assert_called_once()
        run.debit_budget.assert_called_once_with(usage)
    else:
        with pytest.raises(
            PermissionDenied if not readable else ValueError,
            match="cannot read" if not readable else "requires a model with one of these uses",
        ):
            invoke()
        provider_call.assert_not_called()
        run.debit_budget.assert_not_called()
    read.assert_called_once_with(actor)
    read.return_value.has_access.assert_called_once_with("read")






@pytest.mark.parametrize("role", ["mapping", "recognition"])
def test_deterministic_process_denies_unreadable_models_before_consuming_config(
    role: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = object()
    model = _InferenceModel(name="test", model_use="multimodal", status="available")
    read = MagicMock(return_value=SimpleNamespace(has_access=MagicMock(return_value=False)))
    monkeypatch.setattr(model, "with_actor", read)
    monkeypatch.setattr(service, "current_actor", lambda: actor)
    fingerprint = MagicMock()
    authorize = MagicMock()
    monkeypatch.setattr(service, "_model_fingerprint", fingerprint)
    monkeypatch.setattr(service, "_authorize", authorize)
    models = {"model" if role == "mapping" else "recognition_model": model}

    with pytest.raises(PermissionDenied, match="cannot read"):
        service.process(
            object(), (), schema=SCHEMA, authorized_target=object(), profile="none",  **models
        )

    read.assert_called_once_with(actor)
    read.return_value.has_access.assert_called_once_with("read")
    fingerprint.assert_not_called()
    authorize.assert_not_called()




@pytest.mark.parametrize("correspondence_hold", [False, True])
@pytest.mark.parametrize("denial", ["read", "approval", "capability"])
def test_infer_checks_model_before_reuse_or_retained_evidence(
    correspondence_hold: bool, denial: str, monkeypatch: pytest.MonkeyPatch, settings,
) -> None:
    actor = object()
    model = _InferenceModel(
        id=42, name="test", model_use="image" if denial == "capability" else "multimodal", status="available",
    )
    monkeypatch.setattr(
        model, "with_actor", lambda value: SimpleNamespace(has_access=lambda permission: denial != "read"),
    )
    identity = {"provider": "provider", "backend": "test", "endpoint": "local", "model": "test"}
    monkeypatch.setattr(model, "deployment_identity", lambda **kwargs: identity)
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"mapping": [] if denial == "approval" else [identity]}

    class Base(SimpleNamespace):
        def with_actor(self, value):
            return self

        def has_access(self, permission):
            return True

    base = Base(
        pk=7, sqid="ext_base", revision=1, status="failed" if correspondence_hold else "succeeded",
        awaiting_correspondence=correspondence_hold,
    )
    operation = SimpleNamespace(
        request_key="retained-operation",
        input={"base_extraction_id": base.sqid, "base_revision": base.revision, "model_id": str(model.sqid)},
    )
    monkeypatch.setattr(service.apps, "get_model", lambda *args: Base)
    monkeypatch.setattr(service, "current_actor", lambda: actor)
    monkeypatch.setattr(service, "external_operation_request", lambda *args, **kwargs: operation)
    evidence, fingerprint, provider = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(service, "_retained_evidence", evidence)
    monkeypatch.setattr(service, "_model_fingerprint", fingerprint)
    monkeypatch.setattr(service, "map_text_parts", provider)

    with pytest.raises(ValueError if denial == "capability" else PermissionDenied):
        service.infer(base, model=model, authorized_target=base, operation_step_run=object())

    evidence.assert_not_called()
    fingerprint.assert_not_called()
    provider.assert_not_called()
