"""Extraction steps through the DATABASE runner and deterministic providers."""

from __future__ import annotations

import hashlib
import io
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
import yaml
from django.core.exceptions import ValidationError
from PIL import Image
from rebac import actor_context

from angee.base.scoping import system_queryset
from angee.workflows.autoconfig import SETTINGS as WORKFLOW_SETTINGS
from angee.workflows.definition import Definition
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import StepAttempt, WorkflowRun
from angee.workflows_extraction.contracts import (
    DocumentPart,
    ExtractionPartKind,
    MappingResult,
    PipelineError,
    RecognitionResult,
    Result,
)
from angee.workflows_extraction.inference import derive_text_claims
from angee.workflows_extraction.profiles import ExtractionProfile
from angee.workflows_extraction.providers import (
    ExtractionProvider,
    NativeExtractionProvider,
    PageCarrier,
    PreparedDocument,
    RecognitionOutput,
    SourceSnapshot,
)
from angee.workflows_extraction.steps import (
    CollectCarriersStep,
    InferEvidenceStep,
    PreparePagesStep,
    ProcessEvidenceStep,
    RecognizePageStep,
)
from tests.agents_models import InferenceModel, InferenceProvider
from tests.conftest import File, MimeType, make_integration
from tests.extraction_models import Extraction
from tests.test_extraction_models import evidence as evidence
from tests.test_storage import drive as drive

SCHEMA = {
    "$id": "urn:test:step-evidence",
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


class TextProfile(ExtractionProfile):
    key = "step_text"

    def process_parts(self, sources, parts, schema, *, config, recognition_used=False):
        value = {"text": "\n".join(str(part.value) for part in parts)}
        return Result(
            value,
            tuple(parts),
            derive_text_claims(value, parts),
            ("recognition",) if recognition_used else (),
            provider_metadata={"unresolved_reasons": config.get("unresolved_reasons", [])},
        )

    def normalize_inference_candidate(
        self, sources, parts, schema, *, value, claims, metadata, config, recognition_used=False
    ):
        return Result(value, tuple(parts), claims, ("mapping",), provider_metadata=metadata)


class DeterministicProvider(ExtractionProvider):
    """No storage or network effects; retained test text is a stable fixture."""

    key = "deterministic"
    external_io = False
    calls: list[str] = []

    def prepare(self, files, message_parts, *, profile, config, actor):
        self.calls.append("prepare")
        file, text = files[0], "Retained note"
        part = DocumentPart(
            0,
            0,
            "text/plain",
            ExtractionPartKind.RECOGNIZED_TEXT if config.get("recognized") else ExtractionPartKind.NATIVE_TEXT,
            text,
            "test",
            hashlib.sha256(text.encode()).hexdigest(),
        )
        return PreparedDocument(
            sources=[
                SourceSnapshot(
                    kind="file", public_id=str(file.sqid), content_hash=file.content_hash, mime_type="text/plain"
                )
            ],
            pages=[PageCarrier(source_position=0, page_position=0)],
            parts=[part],
        )

    def recognize(self, page, file, model, *, config):
        self.calls.append("recognize")
        return RecognitionResult("Retained note", provider_metadata={"provider": self.key})

    def infer(self, parts, schema, model, *, config):
        self.calls.append("infer")
        if config.get("fail"):
            raise PipelineError("Deterministic inference failure.", stage="mapping_request", code="provider_failure")
        value = {"text": "Retained note"}
        return MappingResult(value, derive_text_claims(value, parts), {"provider": self.key})


@pytest.fixture
def step_evidence(execution, drive, settings, register_step):
    actor, _sent = execution
    for step in (PreparePagesStep, RecognizePageStep, CollectCarriersStep, ProcessEvidenceStep, InferEvidenceStep):
        register_step(step)
    settings.ANGEE_EXTRACTION_PROFILE_CLASSES = {
        **settings.ANGEE_EXTRACTION_PROFILE_CLASSES,
        TextProfile.key: f"{__name__}.TextProfile",
    }
    settings.ANGEE_EXTRACTION_PROVIDER_CLASSES = {
        **settings.ANGEE_EXTRACTION_PROVIDER_CLASSES,
        DeterministicProvider.key: f"{__name__}.DeterministicProvider",
    }
    DeterministicProvider.calls = []
    provider = make_integration("extraction-test", model=InferenceProvider, backend_class="manual", name="Test")
    with actor_context(actor):
        InferenceModel.objects.create(provider=provider, name="deterministic", model_use="multimodal")
        file = File.objects.ingest_bytes(b"Retained note", filename="note.txt", drive_id=str(drive.sqid))
    return actor, file


def execute(step, value, actor, *, provider="deterministic"):
    node = {"step": step.key}
    if step.config_model is not None:
        node["config"] = {"provider": provider}
    workflow = load_workflow(
        {"nodes": {"entry": node}, "results": [{"from": "entry"}]}, key=f"test_{step.key}_{provider}", actor=actor
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, input=value)
    run_until(run)
    return system_queryset(WorkflowRun).get(pk=run.pk)


def prepare_and_process(actor, file, *, profile_config=None):
    source = {
        "files": [str(file.sqid)],
        "target_model": "storage.File",
        "target_id": str(file.sqid),
        "profile": TextProfile.key,
        "profile_config": profile_config or {},
    }
    prepared = execute(PreparePagesStep, source, actor)
    assert prepared.status == "succeeded", list(system_queryset(StepAttempt).values_list("error", flat=True))
    collected = execute(CollectCarriersStep, {"prepared": prepared.output["prepared"], "recognition": []}, actor)
    assert collected.status == "succeeded"
    return execute(
        ProcessEvidenceStep,
        {
            **collected.output,
            "schema": SCHEMA,
            "profile": source["profile"],
            "profile_config": source["profile_config"],
            "target_model": source["target_model"],
            "target_id": source["target_id"],
            "model_id": str(system_queryset(InferenceModel).get().sqid),
            "recognition_model_id": str(system_queryset(InferenceModel).get().sqid)
            if (profile_config or {}).get("recognized")
            else None,
        },
        actor,
    )


def test_preparation_runs_once_and_retains_native_evidence(step_evidence):
    actor, file = step_evidence
    run = prepare_and_process(actor, file)
    assert run.status == "succeeded", list(system_queryset(StepAttempt).values_list("error", flat=True))
    assert run.outcome == "processed"
    row = system_queryset(Extraction).get(sqid=run.output["extraction_id"])
    assert row.result == {"text": "Retained note"}
    with actor_context(actor):
        assert row.sources.count() == row.pages.count() == row.parts.count() == 1
    assert DeterministicProvider.calls == ["prepare"]


def test_recognition_runs_with_registered_pure_provider(step_evidence):
    actor, file = step_evidence
    page = PageCarrier(
        source_position=0,
        page_position=0,
        image_file_id=str(file.sqid),
        image_digest=file.content_hash,
        width=20,
        height=20,
    )
    run = execute(
        RecognizePageStep,
        {"page": page.model_dump(), "model_id": str(system_queryset(InferenceModel).get().sqid)},
        actor,
    )
    assert run.status == "succeeded" and run.outcome == "recognized"
    output = RecognitionOutput.model_validate(run.output)
    prepared = PreparedDocument(sources=[], pages=[page], parts=[])
    parts, holds = prepared.collect([{"index": 0, "outcome": "recognized", "output": run.output}])
    assert output.part.value == parts[0].value == "Retained note" and holds == []
    with pytest.raises(ValidationError, match="indices"):
        prepared.collect([{"index": 1, "outcome": "error", "error": "unavailable"}])
    wrong = replace(output.part, source_position=1)
    with pytest.raises(ValidationError, match="different page"):
        prepared.collect(
            [
                {
                    "index": 0,
                    "outcome": "recognized",
                    "output": RecognitionOutput(page=page, part=wrong).model_dump(mode="json"),
                }
            ]
        )
    assert prepared.collect([{"index": 0, "outcome": "error", "error": "unavailable"}])[1]
    assert DeterministicProvider.calls == ["recognize"]


@pytest.mark.parametrize("step", [PreparePagesStep, RecognizePageStep, InferEvidenceStep])
def test_native_providers_are_blocked_before_database_effects(step_evidence, step):
    actor, file = step_evidence
    if step is PreparePagesStep:
        value = {
            "files": [str(file.sqid)],
            "target_model": "storage.File",
            "target_id": str(file.sqid),
            "profile": TextProfile.key,
        }
    elif step is RecognizePageStep:
        value = {"page": PageCarrier(source_position=0, page_position=0, image_file_id=str(file.sqid)).model_dump()}
    else:
        processed = prepare_and_process(actor, file, profile_config={"unresolved_reasons": ["needs_mapping"]})
        value = {
            "base_extraction_id": processed.output["extraction_id"],
            "base_revision": 1,
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        }
    run = execute(step, value, actor, provider="native")
    assert run.status == "failed"
    assert "L2 IO" in system_queryset(StepAttempt).get(step_run__run=run).error


@pytest.mark.parametrize("fail", [False, True])
def test_inference_retains_a_successor_including_provider_failure(step_evidence, fail):
    actor, file = step_evidence
    processed = prepare_and_process(
        actor, file, profile_config={"unresolved_reasons": ["needs_mapping"], "mapping_config": {"fail": fail}}
    )
    run = execute(
        InferEvidenceStep,
        {
            "base_extraction_id": processed.output["extraction_id"],
            "base_revision": 1,
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        },
        actor,
    )
    assert run.status == "succeeded", list(system_queryset(StepAttempt).values_list("error", flat=True))
    assert run.outcome == ("inference_failed" if fail else "inferred")
    assert run.output["revision"] == 2 and system_queryset(Extraction).count() == 2
    assert DeterministicProvider.calls == ["prepare", "infer"]
    stale = execute(
        InferEvidenceStep,
        {
            "base_extraction_id": processed.output["extraction_id"],
            "base_revision": 1,
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        },
        actor,
    )
    assert stale.outcome == "superseded" and stale.output["superseded_by"] == run.output["extraction_id"]
    assert DeterministicProvider.calls == ["prepare", "infer"]


def test_native_text_acquisition_drops_script_and_checks_digest():
    content = b"<p>Retained note</p><script>ignore</script>"
    row = SimpleNamespace(
        sqid="test_file",
        upload_state="ready",
        size_bytes=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
        mime_type=SimpleNamespace(mime_type="text/html"),
        open_stream=lambda: io.BytesIO(content),
    )
    provider = NativeExtractionProvider()
    prepared = provider.prepare([row], [], profile=TextProfile(), config={}, actor=None)
    assert prepared.parts[0].value == "Retained note" and prepared.recognition_pages == []
    row.content_hash = "0" * 64
    with pytest.raises(ValidationError, match="bytes changed"):
        provider.prepare([row], [], profile=TextProfile(), config={}, actor=None)


def test_inference_preserves_the_retained_recognition_model(step_evidence):
    actor, file = step_evidence
    processed = prepare_and_process(
        actor, file, profile_config={"unresolved_reasons": ["needs_mapping"], "recognized": True}
    )
    recognized = system_queryset(Extraction).get(sqid=processed.output["extraction_id"])
    model = system_queryset(InferenceModel).get()
    run = execute(
        InferEvidenceStep,
        {
            "base_extraction_id": str(recognized.sqid),
            "base_revision": recognized.revision,
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        },
        actor,
    )
    assert run.status == "succeeded" and run.outcome == "inferred"
    successor = system_queryset(Extraction).get(sqid=run.output["extraction_id"])
    assert successor.recognition_model_id == model.pk and successor.model_id == model.pk
    assert set(successor.used_model_roles) == {"mapping", "recognition"}


def test_explicit_correspondence_finalizes_the_held_candidate_without_inference(evidence, step_evidence):
    retain, values = evidence
    actor, _file = step_evidence
    first = retain()
    candidate = deepcopy(values["result"].value)
    candidate["documents"][0]["lines"].reverse()
    held = retain(base=first, request_key="held", result=replace(values["result"], value=candidate))
    assert held.awaiting_correspondence
    document = first.document_refs[0]
    run = execute(
        InferEvidenceStep,
        {
            "base_extraction_id": str(held.sqid),
            "base_revision": held.revision,
            "allow_inference": False,
            "target_model": "storage.File",
            "target_id": str(values["target"].sqid),
            "identity_mapping": {
                "/documents/0": document.identity,
                "/documents/0/lines/0": document.lines[1].identity,
                "/documents/0/lines/1": document.lines[0].identity,
            },
        },
        actor,
    )
    assert run.status == "succeeded" and run.outcome == "inferred"
    finalized = system_queryset(Extraction).get(sqid=run.output["extraction_id"])
    assert finalized.result == held.result and finalized.revision == held.revision + 1
    assert DeterministicProvider.calls == []


def test_unreadable_original_source_blocks_inference_before_provider_call(step_evidence):
    admin, source = step_evidence
    processed = prepare_and_process(admin, source)
    original = system_queryset(Extraction).get(sqid=processed.output["extraction_id"])
    model = system_queryset(InferenceModel).get()
    with actor_context(admin):
        reader = model.provider.owner
    with actor_context(reader):
        target = File.objects.ingest_bytes(
            b"Separate target", filename="target.txt", owner_id=reader.pk, drive_id=str(source.drive.sqid)
        )
    with actor_context(admin):
        base = Extraction.objects.retain_result(
            sources=original.document_sources(),
            result=Result(
                original.result,
                original.document_parts(),
                dict(original.claims),
                provider_metadata={"unresolved_reasons": ["needs_mapping"]},
            ),
            target=target,
            actor=admin,
            profile=TextProfile.key,
            schema=SCHEMA,
            model=model,
            request_key="separate_target",
        )
    assert base.with_actor(reader).has_access("read") and model.with_actor(reader).has_access("read")
    assert not source.with_actor(reader).has_access("read")
    workflow = load_workflow(
        {"nodes": {"entry": {"step": InferEvidenceStep.key, "config": {"provider": DeterministicProvider.key}}}},
        key="protected_source",
        actor=admin,
    )
    workflow.with_actor(admin).grant_record_access("starter", reader)
    run = WorkflowRun.objects.start(
        workflow,
        actor=reader,
        input={
            "base_extraction_id": str(base.sqid),
            "base_revision": base.revision,
            "target_model": "storage.File",
            "target_id": str(target.sqid),
        },
    )
    run_until(run)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == "failed"
    assert "Read access" in system_queryset(StepAttempt).get(step_run__run=run).error
    assert DeterministicProvider.calls == ["prepare"]


def test_unsupported_source_retains_an_explicit_source_hold(step_evidence):
    actor, source = step_evidence
    with actor_context(actor):
        file = File.objects.ingest_bytes(b"\x00opaque", filename="source.bin", drive_id=str(source.drive.sqid))
        prepared = NativeExtractionProvider().prepare([file], [], profile=TextProfile(), config={}, actor=actor)
    collected = execute(CollectCarriersStep, {"prepared": prepared.model_dump(mode="json"), "recognition": []}, actor)
    assert collected.outcome == "source_hold"
    run = execute(
        ProcessEvidenceStep,
        {
            **collected.output,
            "schema": SCHEMA,
            "profile": TextProfile.key,
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        },
        actor,
    )
    assert run.status == "succeeded" and run.outcome == "source_hold"
    row = system_queryset(Extraction).get(sqid=run.output["extraction_id"])
    assert row.status == "failed" and row.unresolved_reasons == ("unsupported_media_type:0",)
    inferred = execute(
        InferEvidenceStep,
        {
            "base_extraction_id": str(row.sqid),
            "base_revision": row.revision,
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        },
        actor,
    )
    assert inferred.outcome == "source_unavailable" and DeterministicProvider.calls == []


def test_partial_recognition_failure_blocks_inference_before_provider_call(step_evidence):
    actor, file = step_evidence
    prepared = DeterministicProvider().prepare([file], [], profile=TextProfile(), config={}, actor=actor)
    prepared.pages.append(
        PageCarrier(
            source_position=0,
            page_position=1,
            image_file_id=str(file.sqid),
            image_digest=file.content_hash,
            width=20,
            height=20,
        )
    )
    collected = execute(
        CollectCarriersStep,
        {
            "prepared": prepared.model_dump(mode="json"),
            "recognition": [{"index": 0, "outcome": "error", "error": "unavailable"}],
        },
        actor,
    )
    processed = execute(
        ProcessEvidenceStep,
        {
            **collected.output,
            "schema": SCHEMA,
            "profile": TextProfile.key,
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        },
        actor,
    )
    assert processed.status == "succeeded" and processed.outcome == "source_hold"
    base = system_queryset(Extraction).get(sqid=processed.output["extraction_id"])
    with actor_context(actor):
        assert base.document_parts() and base.error_code == "source_hold"
    inferred = execute(
        InferEvidenceStep,
        {
            "base_extraction_id": str(base.sqid),
            "base_revision": base.revision,
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        },
        actor,
    )
    assert inferred.status == "succeeded" and inferred.outcome == "source_unavailable"
    assert DeterministicProvider.calls == ["prepare"]


@pytest.mark.parametrize("media_type", ["image/png", "application/pdf"])
def test_native_acquisition_retains_bounded_rasters(step_evidence, media_type):
    actor, source = step_evidence
    content = io.BytesIO()
    if media_type == "image/png":
        with Image.new("RGB", (120, 80), "white") as image:
            image.save(content, format="PNG")
        filename = "page.png"
    else:
        with pdfium.PdfDocument.new() as document:
            document.new_page(120, 80).close()
            document.save(content)
        filename = "page.pdf"
    with actor_context(actor):
        MimeType.objects.get_or_create(mime_type=media_type, defaults={"category": "document", "label": media_type})
        file = File.objects.ingest_bytes(content.getvalue(), filename=filename, drive_id=str(source.drive.sqid))
        prepared = NativeExtractionProvider().prepare(
            [file],
            [],
            profile=TextProfile(),
            config={"max_edge": 60},
            actor=actor,
        )
        page = prepared.recognition_pages[0]
        retained = File.objects.get(sqid=page.image_file_id)
        assert max(page.width, page.height) == 60
        assert retained.content_hash == page.image_digest and retained.upload_state == "ready"
        assert page.image(retained).image_bytes


def test_document_extraction_installation_awaits_l4(execution, register_step, settings):
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_STEP_CLASSES = dict(WORKFLOW_SETTINGS["ANGEE_WORKFLOW_STEP_CLASSES"])
    for step in (PreparePagesStep, RecognizePageStep, CollectCarriersStep, ProcessEvidenceStep, InferEvidenceStep):
        register_step(step)
    path = Path(__file__).parents[1] / "addons/angee/workflows_extraction/resources/install/100_workflows.workflow.yaml"
    fields = yaml.safe_load(path.read_text())["rows"][0]["fields"]
    _definition, issues = Definition.check(fields["draft"])
    if any(issue.path == ["nodes", "map_pages", "body"] for issue in issues):
        pytest.skip("L4: Definition does not yet admit the nested map body or its built-in step.")
    assert not issues
    workflow = load_workflow(fields["draft"], key=fields["key"], actor=actor)
    assert workflow.published_id is not None
