"""Extraction steps through the IO runner and domain inference seams."""

from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pypdfium2 as pdfium
import pytest
import yaml
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from rebac import actor_context

from angee.base.scoping import system_queryset
from angee.extraction.acquisition import (
    ExtractionConfig,
    PageCarrier,
    PartCarrier,
    PreparedDocument,
    RecognitionOutput,
    SourceSnapshot,
    prepare_pages,
)
from angee.extraction.contracts import (
    DocumentPart,
    ExtractionPartKind,
    MappingResult,
    PipelineError,
    RecognitionResult,
    Result,
)
from angee.extraction.inference import derive_text_claims
from angee.extraction.profiles import ExtractionProfile
from angee.workflows.definition import Definition
from angee.workflows.maps import MapItem
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import StepArtifact, StepAttempt, StepRun, WorkflowRun
from angee.workflows_extraction.steps import (
    InferEvidenceStep,
    PreparePagesStep,
    ProcessEvidenceInput,
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


class TextProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unresolved_reasons: list[str] = Field(default_factory=list, max_length=10)


class TextProfile(ExtractionProfile):
    key = "step_text"
    config_model = TextProfileConfig

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


@pytest.mark.parametrize("config", [
    {"profile": "none", "schema": SCHEMA},
    {"profile": "missing_profile", "schema": SCHEMA},
    {"profile": "step_text", "schema": {}},
    {"profile": "step_text", "schema": SCHEMA, "profile_config": {"unknown": True}},
])
def test_publication_rejects_invalid_extraction_declarations(config):
    with pytest.raises(ValidationError):
        ProcessEvidenceStep.parse_config(config)


class DeterministicExtraction:
    """Retained test text without network calls."""

    recognized = False
    fail = False
    key = "deterministic"
    calls: list[str] = []

    def prepare(self, files, message_parts, *, profile, config, actor, heartbeat):
        self.calls.append("prepare")
        heartbeat()
        file, text = files[0], "Retained note"
        part = DocumentPart(
            0,
            0,
            "text/plain",
            ExtractionPartKind.RECOGNIZED_TEXT if type(self).recognized else ExtractionPartKind.NATIVE_TEXT,
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
            parts=[PartCarrier.retain(part, actor=actor, drive_id=str(file.drive.sqid))],
        )

    def recognize(self, page, file, model, *, config):
        self.calls.append("recognize")
        return RecognitionResult("Retained note", provider_metadata={"provider": self.key})

    def infer(self, parts, schema, model, *, config):
        self.calls.append("infer")
        if type(self).fail:
            raise PipelineError("Deterministic inference failure.", stage="mapping_request", code="provider_failure")
        value = {"text": "Retained note"}
        return MappingResult(value, derive_text_claims(value, parts), {"provider": self.key})


@pytest.fixture
def step_evidence(execution, drive, monkeypatch):
    actor, _sent = execution
    DeterministicExtraction.calls = []
    DeterministicExtraction.recognized = False
    DeterministicExtraction.fail = False
    from angee.extraction.managers import ExtractionManager
    from angee.workflows_extraction import steps as step_module
    def prepare(_manager, *args, **kwargs):
        return DeterministicExtraction().prepare(*args, **kwargs)

    def recognize(page, file, model, *, config):
        return DeterministicExtraction().recognize(page, file, model, config=config)

    def infer(parts, schema, model, *, config):
        return DeterministicExtraction().infer(parts, schema, model, config=config)

    monkeypatch.setattr(ExtractionManager, "prepare_pages", prepare)
    monkeypatch.setattr(step_module, "recognize_page", recognize)
    monkeypatch.setattr(step_module, "map_parts", infer)
    provider = make_integration("extraction-test", model=InferenceProvider, backend_class="manual", name="Test")
    with actor_context(actor):
        InferenceModel.objects.create(provider=provider, name="deterministic", model_use="multimodal")
        file = File.objects.ingest_bytes(b"Retained note", filename="note.txt", drive_id=str(drive.sqid))
    return actor, file


def execute(step, value, actor, *, config=None):
    node = {"step": step.key}
    config = dict(config or {})
    if step is PreparePagesStep:
        DeterministicExtraction.recognized = config.pop("recognized", False)
    if step is InferEvidenceStep:
        DeterministicExtraction.fail = config.pop("fail", False)
    if step.config_model is not None:
        node["config"] = (
            {"schema": SCHEMA, "profile": TextProfile.key} if step is ProcessEvidenceStep else {}
        )
        if step is PreparePagesStep:
            node["config"]["profile"] = TextProfile.key
        node["config"].update(config)
    workflow = load_workflow(
        {"nodes": {"entry": node}, "results": [{"from": "entry"}]}, key=f"test_{step.key}", actor=actor
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, input=value)
    run_until(run)
    return system_queryset(WorkflowRun).get(pk=run.pk)


def prepare_and_process(actor, file, *, profile_config=None, recognized=False):
    source = {
        "files": [str(file.sqid)],
        "target_model": "storage.File",
        "target_id": str(file.sqid),
    }
    prepared = execute(PreparePagesStep, source, actor, config={"recognized": recognized})
    assert prepared.status == "succeeded", list(system_queryset(StepAttempt).values_list("error", flat=True))
    return execute(
        ProcessEvidenceStep,
        {
            "prepared": prepared.output["prepared"],
            "recognition": [],
            "target_model": source["target_model"],
            "target_id": source["target_id"],
            "model_id": str(system_queryset(InferenceModel).get().sqid),
            "recognition_model_id": str(system_queryset(InferenceModel).get().sqid)
            if recognized
            else None,
        },
        actor,
        config={"profile_config": profile_config or {}},
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
    assert DeterministicExtraction.calls == ["prepare"]
    assert system_queryset(StepArtifact).filter(step_run__run=run).count() == 1


def test_workflow_reader_cannot_obtain_document_text_from_step_rows(step_evidence):
    actor, file = step_evidence
    processed = prepare_and_process(actor, file)
    reader = get_user_model().objects.create_user(username="workflow-reader")
    assert not file.with_actor(reader).has_access("read")
    runs = list(system_queryset(WorkflowRun).all())
    for run in runs:
        run.with_actor(actor).grant_record_access("reader", reader)
    with actor_context(reader):
        assert WorkflowRun.objects.filter(pk=processed.pk).exists()
        assert StepRun.objects.count() == 2
        retained = [
            list(StepRun.objects.values("input", "output", "state", "wait_reason")),
            [{"error": attempt.error, "stacktrace": attempt.stacktrace} for attempt in StepAttempt.objects.all()],
            [{"input": row.input, "output": row.output, "error": row.error} for row in WorkflowRun.objects.all()],
        ]
        assert "Retained note" not in json.dumps(retained)
        prepared = next(run.output["prepared"] for run in runs if "prepared" in run.output)
        assert not File.objects.filter(sqid=prepared["parts"][0]["file_id"]).exists()


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
    value = ProcessEvidenceInput(
        prepared=prepared,
        recognition=[MapItem[RecognitionOutput](index=0, outcome="recognized", output=output)],
        target_model="storage.File", target_id=str(file.sqid),
    )
    parts, holds = value.carriers()
    assert output.part == parts[0] and holds == []
    assert "Retained note" not in json.dumps(run.output)
    with actor_context(actor):
        envelope = system_queryset(File).get(sqid=output.part.file_id).read_verified(max_bytes=10000)
        assert json.loads(envelope)["value"] == "Retained note"
    with pytest.raises(ValidationError, match="indices"):
        value.model_copy(update={"recognition": [
            MapItem[RecognitionOutput](index=1, outcome="error", error="unavailable"),
        ]}).carriers()
    wrong = output.part.model_copy(update={"source_position": 1})
    with pytest.raises(ValidationError, match="different page"):
        value.model_copy(update={"recognition": [
            MapItem[RecognitionOutput](
                index=0, outcome="recognized", output=RecognitionOutput(page=page, part=wrong),
            ),
        ]}).carriers()
    assert value.model_copy(update={"recognition": [
        MapItem[RecognitionOutput](index=0, outcome="error", error="unavailable"),
    ]}).carriers()[1] == ["recognition_unavailable:0:0"]
    assert DeterministicExtraction.calls == ["recognize"]
    assert system_queryset(StepAttempt).get(step_run__run=run).effect_started_at is not None


@pytest.mark.parametrize("fail", [False, True])
def test_inference_retains_a_successor_including_provider_failure(step_evidence, fail):
    actor, file = step_evidence
    processed = prepare_and_process(
        actor, file, profile_config={"unresolved_reasons": ["needs_mapping"]}
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
        config={"fail": fail},
    )
    assert run.status == "succeeded", list(system_queryset(StepAttempt).values_list("error", flat=True))
    assert run.outcome == ("inference_failed" if fail else "inferred")
    assert run.output["revision"] == 2 and system_queryset(Extraction).count() == 2
    assert DeterministicExtraction.calls == ["prepare", "infer"]
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
    assert DeterministicExtraction.calls == ["prepare", "infer"]


def test_native_text_acquisition_drops_script_and_checks_digest(step_evidence):
    actor, source = step_evidence
    content = b"<p>Retained note</p><script>ignore</script>"
    with actor_context(actor):
        MimeType.objects.get_or_create(mime_type="text/html", defaults={"category": "document", "label": "HTML"})
        row = File.objects.ingest_bytes(content, filename="note.html", drive_id=str(source.drive.sqid))
        prepared = prepare_pages(
            [row], [], profile=TextProfile(), config=ExtractionConfig(), actor=actor, heartbeat=lambda: None,
        )
        retained = File.objects.get(sqid=prepared.parts[0].file_id)
        assert json.loads(retained.read_verified(max_bytes=10000))["value"] == "Retained note"
        assert prepared.recognition_pages == []
        row.content_hash = "0" * 64
        with pytest.raises(ValidationError, match="stored bytes"):
            prepare_pages(
                [row], [], profile=TextProfile(), config=ExtractionConfig(), actor=actor, heartbeat=lambda: None,
            )


def test_inference_preserves_the_retained_recognition_model(step_evidence):
    actor, file = step_evidence
    processed = prepare_and_process(
        actor, file, profile_config={"unresolved_reasons": ["needs_mapping"]}, recognized=True,
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
    assert DeterministicExtraction.calls == []


def test_cross_target_original_source_is_rejected_before_inference(step_evidence):
    admin, source = step_evidence
    processed = prepare_and_process(admin, source)
    original = system_queryset(Extraction).get(sqid=processed.output["extraction_id"])
    reader = get_user_model().objects.create_user(username="separate-target-owner")
    with actor_context(reader):
        target = File.objects.ingest_bytes(
            b"Separate target", filename="target.txt", owner_id=reader.pk, drive_id=str(source.drive.sqid)
        )
    with actor_context(admin), pytest.raises(ValidationError, match="belongs to the extraction target"):
        Extraction.objects.retain_result(
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
            request_key="separate_target",
        )
    assert "infer" not in DeterministicExtraction.calls
    assert DeterministicExtraction.calls == ["prepare"]


def test_unsupported_source_retains_an_explicit_source_hold(step_evidence):
    actor, source = step_evidence
    with actor_context(actor):
        file = File.objects.ingest_bytes(b"\x00opaque", filename="source.bin", drive_id=str(source.drive.sqid))
        prepared = prepare_pages(
            [file], [], profile=TextProfile(), config=ExtractionConfig(), actor=actor,
            heartbeat=lambda: None,
        )
    run = execute(
        ProcessEvidenceStep,
        {
            "prepared": prepared.model_dump(mode="json"),
            "recognition": [],
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        },
        actor,
    )
    assert run.status == "succeeded" and run.outcome == "source_hold"
    row = system_queryset(Extraction).get(sqid=run.output["extraction_id"])
    assert row.outcome["kind"] == "failed" and row.unresolved_reasons == ("unsupported_media_type:0",)
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
    assert inferred.outcome == "source_unavailable" and DeterministicExtraction.calls == []


def test_partial_recognition_failure_blocks_inference_before_provider_call(step_evidence):
    actor, file = step_evidence
    with actor_context(actor):
        prepared = DeterministicExtraction().prepare(
            [file], [], profile=TextProfile(), config=ExtractionConfig(), actor=actor,
            heartbeat=lambda: None,
        )
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
    processed = execute(
        ProcessEvidenceStep,
        {
            "prepared": prepared.model_dump(mode="json"),
            "recognition": [{"index": 0, "outcome": "error", "error": "unavailable"}],
            "target_model": "storage.File",
            "target_id": str(file.sqid),
        },
        actor,
    )
    assert processed.status == "succeeded" and processed.outcome == "source_hold"
    base = system_queryset(Extraction).get(sqid=processed.output["extraction_id"])
    with actor_context(actor):
        assert base.document_parts() and base.outcome["code"] == "source_hold"
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
    assert DeterministicExtraction.calls == ["prepare"]


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
        prepared = prepare_pages(
            [file],
            [],
            profile=TextProfile(),
            config=ExtractionConfig(max_edge=60),
            actor=actor,
            heartbeat=lambda: None,
        )
        page = prepared.recognition_pages[0]
        retained = File.objects.get(sqid=page.image_file_id)
        assert max(page.width, page.height) == 60
        assert retained.content_hash == page.image_digest and retained.upload_state == "ready"
        assert page.image(retained).image_bytes


@pytest.mark.parametrize("with_inference", [False, True])
def test_document_extraction_installs(execution, with_inference):
    """The shipped graph can feed its exact retained revision into native inference."""
    actor, _sent = execution
    path = Path(__file__).parents[1] / "addons/angee/workflows_extraction/resources/install/100_workflows.workflow.yaml"
    fields = yaml.safe_load(path.read_text())["rows"][0]["fields"]
    if with_inference:
        fields["draft"]["nodes"]["process_evidence"]["next"] = {
            "processed": "infer_evidence", "source_hold": "infer_evidence",
        }
        fields["draft"]["nodes"]["infer_evidence"] = {
            "step": "infer_evidence",
            "input": {
                "base_extraction_id": {"from": "process_evidence", "path": ["extraction_id"]},
                "base_revision": {"from": "process_evidence", "path": ["revision"]},
                "target_model": {"from": "input", "path": ["target_model"]},
                "target_id": {"from": "input", "path": ["target_id"]},
            },
        }
        fields["draft"]["results"] = [{"from": "infer_evidence"}]
    _definition, issues = Definition.check(fields["draft"])
    assert not issues
    workflow = load_workflow(fields["draft"], key=fields["key"], actor=actor)
    assert workflow.published_id is not None
