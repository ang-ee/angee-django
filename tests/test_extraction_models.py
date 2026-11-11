"""Durable evidence, identity continuity, and retention authorization proofs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier
from traceback import format_exception
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist, PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.db.models import CharField
from django.db.models.deletion import PROTECT
from rebac import actor_context, generic_target, system_context

from angee.base.fields.state import StateField
from angee.base.models.derived import DerivedFrom
from angee.extraction.acquisition import PageCarrier, PartCarrier
from angee.extraction.contracts import (
    DocumentPart,
    ExtractionPartKind,
    Result,
    Source,
)
from angee.extraction.managers import ExtractionManager, StaleExtraction
from angee.extraction.profiles import EvidenceLayout, ExtractionProfile
from angee.provenance.evidence import FactAuthority
from angee.storage.models import File as AbstractFile
from angee.storage.models import FileManager
from angee.workflows_extraction.steps import InferEvidenceInput, InferEvidenceStep
from tests.conftest import Drive, File, create_platform_admin
from tests.extraction_models import Extraction, ExtractionLineage, ExtractionPage, ExtractionPart, ExtractionSource
from tests.lock_waits import submit, wait_for_lock
from tests.mtidemo.models import MtiChild
from tests.test_storage import drive as drive


def test_extraction_storage_shape_has_one_target_and_outcome():
    """The model owns the FK target, revision link and exclusive outcome storage."""
    assert not isinstance(ExtractionLineage._meta.pk, CharField)
    for name in ("lineage", "file", "message"):
        assert Extraction._meta.get_field(name).remote_field.on_delete is PROTECT
    assert "extraction_one_target" in {constraint.name for constraint in Extraction._meta.constraints}
    assert isinstance(ExtractionPart._meta.get_field("kind"), StateField)
    for old in ("lineage_key", "status", "error_code", "provenance", "content_type", "object_id"):
        with pytest.raises(FieldDoesNotExist):
            Extraction._meta.get_field(old)


class NotesProfile(ExtractionProfile):
    """Declare document and line locations independently of their values."""

    key = "retention_notes"
    evidence_layout = EvidenceLayout(document_collection="/documents", line_collection="/lines")


SCHEMA = {
    "$id": "urn:test:extraction:notes",
    "type": "object",
    "required": ["documents"],
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "lines"],
                "properties": {
                    "title": {"type": "string"},
                    "optional": {"type": ["string", "null"]},
                    "lines": {
                        "type": "array",
                        "items": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
                    },
                },
            },
        },
    },
}


@pytest.fixture
def evidence(drive, monkeypatch):
    """Retain real storage-backed evidence."""

    monkeypatch.setattr(File, "rebac_grantable", AbstractFile.rebac_grantable)
    content = b"Note\nFirst line\nSecond line"
    with actor_context(drive.alice):
        source, _created = File.objects.ingest_bytes(content, filename="note.txt", drive_id=str(drive.sqid))
    document_source = Source(0, source.content_hash, "text/plain", content, file=source)
    part = DocumentPart(
        0,
        0,
        "text/plain",
        ExtractionPartKind.NATIVE_TEXT,
        content.decode(),
        "native",
        source.content_hash,
    )
    result = Result(
        value={
            "documents": [
                {
                    "title": "Note",
                    "optional": None,
                    "lines": [{"text": "First line"}, {"text": "Second line"}],
                }
            ]
        },
        parts=(part,),
        claims={"/documents/0/title": [{"part_position": 0, "start": 0, "end": 4}]},
    )
    defaults = {
        "sources": (document_source,),
        "result": result,
        "target": source,
        "actor": drive.alice,
        "profile": NotesProfile.key,
        "profile_config": {},
        "schema": SCHEMA,
        "request_key": "first",
    }

    def retain(**changes):
        values = {**defaults, **changes}
        with actor_context(values["actor"]):
            return Extraction.objects.retain_result(**values)

    with actor_context(drive.alice):
        yield retain, defaults


def test_retention_preserves_sources_parts_pages_and_typed_missing_values(evidence):
    retain, values = evidence
    image = PageCarrier(source_position=0, page_position=0, width=40, height=60, dpi=100)
    row = retain(pages=(image,))

    assert str(row) == str(row.target)

    assert row.revision == 1
    assert row.sources.count() == row.parts.count() == row.pages.count() == 1
    retained_source = row.document_sources()[0]
    assert retained_source.file.pk == values["target"].pk
    assert retained_source.content_hash == values["sources"][0].content_hash
    assert retained_source.mime_type == "text/plain"
    assert row.document_parts()[0].value == values["result"].parts[0].value
    reference = row.document_refs[0]
    document, selected = row.selected_document(reference.identity)
    assert selected == reference and document["title"] == "Note"
    line, line_reference = row.selected_line(reference.identity, reference.lines[1].identity)
    assert line["text"] == "Second line" and line_reference == reference.lines[1]
    assert row.fact("/documents/0/optional") is None
    assert row.fact_authority("/documents/0/title") is FactAuthority.SOURCE
    source = row.sources.get()
    assert isinstance(source, DerivedFrom)
    target = generic_target(values["target"])
    assert (source.content_type, source.object_id) == (target.content_type, target.object_id)
    assert source.record == values["target"]
    with pytest.raises(KeyError):
        row.fact("/documents/0/absent")
    with pytest.raises(KeyError):
        row.selected_line(reference.identity, "unknown")
    assert ExtractionLineage.objects.get(pk=row.lineage_id).head_id == row.pk


def test_revision_reuses_one_protected_part_carrier(evidence):
    retain, values = evidence
    first = retain()
    second = retain(base=first, request_key="next")
    assert isinstance(first.lineage_id, int) and second.lineage_id == first.lineage_id
    assert first.parts.get().carrier_file_id == second.parts.get().carrier_file_id
    assert second.document_parts()[0].value == values["result"].parts[0].value
    with pytest.raises(FieldDoesNotExist):
        ExtractionPart._meta.get_field("value")


def test_inference_retry_returns_its_revision_before_head_comparison(evidence):
    retain, values = evidence
    base = retain()
    attempt = retain(base=base, request_key="retry-attempt")
    later = retain(base=attempt, request_key="later-attempt")
    assert later.pk != attempt.pk
    ctx = SimpleNamespace(
        actor=values["actor"],
        input=InferEvidenceInput(
            base_extraction_id=str(base.sqid), base_revision=base.revision,
            target_model="storage.File", target_id=str(values["target"].sqid),
        ),
        idempotency_key="retry-attempt",
        load=lambda model, public_id: base if model is Extraction else values["target"],
        done=lambda output, *, outcome: (output, outcome),
    )
    output, outcome = InferEvidenceStep().run(ctx)
    assert outcome == "inferred" and output.extraction_id == str(attempt.sqid)


def test_exact_retry_reuses_revision_and_conflicting_request_is_rejected(evidence):
    retain, values = evidence
    first = retain()
    assert retain().pk == first.pk
    changed = deepcopy(values["result"].value)
    changed["documents"][0]["optional"] = "Other"
    with pytest.raises(ValidationError):
        retain(result=replace(values["result"], value=changed))
    assert Extraction.objects.count() == 1


def test_retained_inference_admission_settles_without_reading_sources(evidence, monkeypatch):
    retain, values = evidence
    row = retain()
    unresolved = replace(values["result"], provider_metadata={"unresolved_reasons": ["missing total"]})
    pending = retain(result=unresolved, request_key="unresolved")
    held = retain(result=unresolved, request_key="held", error_code="source_hold")
    monkeypatch.setattr(Extraction, "document_sources", lambda self: pytest.fail("Admission must not read sources."))

    assert row.inference_admission(allow_inference=True, identity_mapping={}) == "unchanged"
    assert pending.inference_admission(allow_inference=False, identity_mapping={}) == "unchanged"
    assert pending.inference_admission(allow_inference=True, identity_mapping={}) == "infer"
    assert row.retained_inference_parts() == row.document_parts()
    assert held.retained_inference_parts() == ()


@pytest.mark.django_db(transaction=True)
def test_carrier_storage_writes_and_reads_precede_the_lineage_transaction(evidence, monkeypatch):
    retain, _values = evidence
    ingest, read = FileManager.ingest_bytes, File.read_verified
    calls = []

    def ingest_outside(self, *args, **kwargs):
        assert not connection.in_atomic_block
        calls.append("write")
        return ingest(self, *args, **kwargs)

    def read_outside(self, *args, **kwargs):
        assert not connection.in_atomic_block
        calls.append("read")
        return read(self, *args, **kwargs)

    monkeypatch.setattr(FileManager, "ingest_bytes", ingest_outside)
    monkeypatch.setattr(File, "read_verified", read_outside)
    row = retain()
    assert calls == ["write", "read"]
    assert row.parts.count() == 1


def test_failed_allocation_discards_only_the_carrier_it_created(evidence, monkeypatch):
    retain, _values = evidence
    with system_context(reason="test extraction carrier inventory"):
        before = set(File.objects.values_list("pk", flat=True))

    def refuse(*args, **kwargs):
        raise ValidationError("allocation refused")

    monkeypatch.setattr(ExtractionManager, "_retain_prepared", refuse)
    with pytest.raises(ValidationError, match="allocation refused"):
        retain()
    with system_context(reason="test extraction carrier cleanup"):
        assert set(File.objects.values_list("pk", flat=True)) == before


def test_failed_allocation_keeps_a_carrier_a_retained_revision_holds(evidence, monkeypatch):
    retain, _values = evidence
    allocate = ExtractionManager._retain_prepared
    retained = []

    def allocate_then_refuse(self, *args, **kwargs):
        retained.append(allocate(self, *args, **kwargs))
        raise ValidationError("the caller refused after allocation")

    monkeypatch.setattr(ExtractionManager, "_retain_prepared", allocate_then_refuse)
    with pytest.raises(ValidationError, match="refused after allocation"):
        retain()
    with system_context(reason="test extraction held carrier"):
        carrier = retained[0].parts.get().carrier_file
        assert File.objects.filter(pk=carrier.pk).exists()
        assert not carrier.purge_if_unretained()


def test_allocation_rechecks_the_source_identity_after_carrier_preparation(evidence, monkeypatch):
    retain, values = evidence
    read = PartCarrier.read
    with system_context(reason="test extraction carrier inventory"):
        before = set(File.objects.values_list("pk", flat=True))

    def change_source(self, file):
        part = read(self, file)
        File._base_manager.filter(pk=values["target"].pk).update(content_hash="b" * 64)
        return part

    monkeypatch.setattr(PartCarrier, "read", change_source)
    with pytest.raises(ValidationError, match="source changed during carrier preparation"):
        retain()
    assert Extraction.objects.count() == 0
    with system_context(reason="test extraction carrier cleanup"):
        assert set(File.objects.values_list("pk", flat=True)) == before


def test_lineage_compare_and_swap_rejects_a_stale_parent(evidence):
    retain, _values = evidence
    first = retain()
    second = retain(base=first, request_key="second")
    assert second.revision == 2
    assert second.document_refs == first.document_refs
    with pytest.raises(ValidationError):
        retain(base=first, request_key="stale")
    assert ExtractionLineage.objects.get(pk=first.lineage_id).head_id == second.pk
    assert Extraction.objects.count() == 2


def test_file_extraction_rejects_sources_outside_its_target(evidence):
    retain, values = evidence
    other, _created = File.objects.ingest_bytes(
        b"Another note",
        filename="other.txt",
        drive_id=str(values["target"].drive.sqid),
    )
    original = values["sources"][0]
    additional = Source(1, other.content_hash, "text/plain", b"Another note", file=other)
    with pytest.raises(ValidationError, match="belongs to the extraction target"):
        retain(sources=(original, additional))


def test_extraction_rejects_targets_outside_file_and_message(evidence):
    """Explicit target fields cannot reinterpret an unrelated row with the same PK."""
    retain, values = evidence
    with system_context(reason="tests extraction unsupported target"):
        child = MtiChild.objects.create(pk=values["target"].pk, title="Unrelated target")
    with pytest.raises(ValidationError, match="file or message"):
        retain(target=child)
    assert Extraction.objects.count() == 0


@pytest.mark.parametrize(
    "invalid",
    [
        "schema",
        "source_position",
        "claim_position",
        "claim_shape",
        "claim_span",
        "source_digest",
        "unknown_role",
        "unconfigured_role",
        "nonfinite_metadata",
        "page_source",
    ],
)
def test_invalid_candidate_rolls_back_the_whole_evidence_family(evidence, invalid):
    retain, values = evidence
    result = values["result"]
    sources = values["sources"]
    pages = ()
    if invalid == "schema":
        result = replace(result, value={"documents": [{"title": 3, "lines": []}]})
    elif invalid == "source_position":
        result = replace(result, parts=(replace(result.parts[0], source_position=9),))
    elif invalid == "claim_position":
        result = replace(result, claims={"/documents/0/title": [{"part_position": 9, "start": 0, "end": 4}]})
    elif invalid == "claim_shape":
        result = replace(result, claims={"/documents/0/title": ["not a claim"]})
    elif invalid == "claim_span":
        result = replace(result, claims={"/documents/0/title": [{"part_position": 0, "start": 0, "end": 999}]})
    elif invalid == "source_digest":
        sources = (replace(sources[0], content_hash="a" * 64),)
    elif invalid == "unknown_role":
        result = replace(result, used_model_roles=("unknown",))
    elif invalid == "unconfigured_role":
        result = replace(result, used_model_roles=("mapping",))
    elif invalid == "nonfinite_metadata":
        result = replace(result, provider_metadata={"duration": float("nan")})
    else:
        pages = (PageCarrier(source_position=99, page_position=0, width=40, height=60, dpi=100),)
    with pytest.raises(ValidationError):
        retain(result=result, sources=sources, pages=pages)
    for model in (Extraction, ExtractionLineage, ExtractionSource, ExtractionPage, ExtractionPart):
        assert model.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_competing_revision_requests_allocate_exactly_one_successor(evidence):
    """Race native PostgreSQL locks; SQLite checks the same CAS serially."""

    retain, _values = evidence
    base = retain()
    parallel = connection.vendor == "postgresql"
    ready = Barrier(2) if parallel else None

    def revise(key):
        close_old_connections()
        try:
            if ready is not None:
                ready.wait(timeout=10)
            try:
                return retain(base=base, request_key=key).pk
            except StaleExtraction:
                return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2 if parallel else 1) as pool:
        results = list(pool.map(revise, ("left", "right")))
    assert results.count(None) == 1
    winner = next(value for value in results if value is not None)
    assert ExtractionLineage.objects.get(pk=base.lineage_id).head_id == winner
    assert list(Extraction.objects.order_by("revision").values_list("revision", flat=True)) == [1, 2]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", ["carrier purge", "source edit"])
def test_retention_waits_for_a_held_file_and_refuses_its_committed_change(evidence, change):
    """Retention locks its source and carrier files before rechecking them, so a held purge or edit wins."""

    if connection.vendor != "postgresql":
        pytest.skip("Row-lock waits need PostgreSQL.")
    retain, values = evidence
    carrier, _created = PartCarrier.retain(
        values["result"].parts[0],
        actor=values["actor"],
        drive_id=str(values["target"].drive.sqid),
    )
    held = File._base_manager.get(sqid=carrier.file_id) if change == "carrier purge" else values["target"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            with system_context(reason="test extraction holds a file"):
                locked = File.system_queryset(lock=("self",)).get(pk=held.pk)
            future, pid = submit(pool, lambda: retain(part_carriers=(carrier,)))
            wait_for_lock(pid, future)
            with system_context(reason="test extraction changes the held file"):
                if change == "carrier purge":
                    locked.purge()
                else:
                    File._base_manager.filter(pk=held.pk).update(content_hash="b" * 64)
        expected = "carrier changed before retention" if change == "carrier purge" else "source changed"
        with pytest.raises(ValidationError, match=expected):
            future.result(timeout=10)
    assert Extraction.objects.count() == 0


def test_explicit_correspondence_preserves_reordered_lines_and_records_retirement(evidence):
    retain, values = evidence
    first = retain()
    reference = first.document_refs[0]
    changed = deepcopy(values["result"].value)
    changed["documents"][0]["lines"] = [{"text": "Second line"}, {"text": "New line"}]
    next_row = retain(
        base=first,
        request_key="correspondence",
        result=replace(values["result"], value=changed),
        identity_mapping={
            "/documents/0": reference.identity,
            "/documents/0/lines/0": reference.lines[1].identity,
            "/documents/0/lines/1": "new",
        },
        retired_identities={reference.lines[0].identity: "Removed from the revised evidence"},
    )
    next_ref = next_row.document_refs[0]
    assert next_ref.identity == reference.identity
    assert next_ref.lines[0].identity == reference.lines[1].identity
    assert next_ref.lines[1].identity not in {item.identity for item in reference.lines}
    assert next_row.retired_identities == [
        {
            "identity": reference.lines[0].identity,
            "kind": "line",
            "reason": "Removed from the revised evidence",
        }
    ]


def test_unknown_correspondence_cannot_create_arbitrary_identities(evidence):
    retain, values = evidence
    first = retain()
    reference = first.document_refs[0]
    with pytest.raises(ValidationError):
        retain(
            base=first,
            request_key="invalid-correspondence",
            result=values["result"],
            identity_mapping={
                "/documents/0": reference.identity,
                "/documents/0/lines/0": "invented",
                "/documents/0/lines/1": reference.lines[1].identity,
            },
        )
    assert Extraction.objects.count() == 1


def test_retained_rows_reject_instance_and_collection_mutation(evidence):
    retain, _values = evidence
    extraction = retain(
        pages=(PageCarrier(source_position=0, page_position=0, width=40, height=60, dpi=100),),
    )
    with system_context(reason="tests.extraction immutable evidence"):
        rows = (
            extraction,
            ExtractionLineage.objects.get(pk=extraction.lineage_id),
            extraction.sources.get(),
            extraction.pages.get(),
            extraction.parts.get(),
        )
        for row in rows:
            with pytest.raises((ValidationError, ValueError)):
                row.save()
            with pytest.raises((ValidationError, ValueError)):
                row.delete()
            for manager in (type(row).objects, type(row)._base_manager):
                with pytest.raises(ValueError, match="only be inserted by its retention owner"):
                    manager.bulk_create([])
                with pytest.raises(ValidationError):
                    manager.filter(pk=row.pk).update(pk=row.pk)
                with pytest.raises(ValidationError):
                    manager.filter(pk=row.pk).delete()
    assert Extraction.objects.count() == 1


@pytest.mark.parametrize("protected", ["source", "target"])
def test_retention_requires_independent_source_and_target_read_access(evidence, protected):
    retain, values = evidence
    outsider = get_user_model().objects.create_user(username="evidence-outsider")
    with actor_context(outsider), system_context(reason="tests.extraction separate source and target"):
        other_drive = Drive.objects.create(
            backend=values["target"].drive.backend,
            slug="private",
            name="Private",
            prefix="private",
            created_by=outsider,
            updated_by=outsider,
        )
        other, _created = File.objects.ingest_bytes(
            b"Other evidence",
            filename="other.txt",
            drive_id=str(other_drive.sqid),
            owner_id=outsider.pk,
        )
    if protected == "source":
        changes = {"sources": (replace(values["sources"][0], file=other),)}
    else:
        changes = {"target": other}
    expected = ValidationError if protected == "source" else PermissionDenied
    with pytest.raises(expected):
        retain(**changes)
    assert Extraction.objects.count() == 0


def test_target_reader_inherits_evidence_access_without_becoming_its_creator(evidence):
    retain, values = evidence
    owner = values["actor"]
    author = create_platform_admin("evidence-author")
    row = retain(actor=author)
    outsider = get_user_model().objects.create_user(username="evidence-reader-outsider")
    assert row.created_by_id == author.pk
    for evidence_row in (row, row.sources.get(), row.parts.get()):
        assert evidence_row.with_actor(owner).has_access("read")
        assert not evidence_row.with_actor(outsider).has_access("read")


def test_target_reader_can_read_bound_parts_but_cannot_share(evidence):
    retain, values = evidence
    owner = values["actor"]
    reader = get_user_model().objects.create_user(username="target-only-reader")
    onward = get_user_model().objects.create_user(username="onward-reader")
    target = values["target"]
    row = retain()
    part = row.parts.get()
    target.with_actor(owner).grant_record_access("viewer", reader)
    assert row.with_actor(reader).has_access("read")
    assert part.with_actor(reader).has_access("read")
    with actor_context(reader), pytest.raises(PermissionDenied):
        row.with_actor(reader).grant_record_access("viewer", onward)
    row.with_actor(owner).grant_record_access("viewer", onward)
    assert row.with_actor(onward).has_access("read")
    assert not part.with_actor(onward).has_access("read")
    target.with_actor(owner).revoke_record_access("viewer", reader)
    assert not part.with_actor(reader).has_access("read")


def test_target_access_uses_protected_field_relation(evidence):
    retain, values = evidence
    author = create_platform_admin("field-author")
    row = retain(actor=author)
    assert row.file_id == values["target"].pk and row.message_id is None
    assert row.with_actor(values["actor"]).has_access("read")


def test_source_and_target_reader_cannot_share_an_extraction_onward(evidence):
    retain, values = evidence
    row = retain()
    reader = get_user_model().objects.create_user(username="source-reader")
    onward = get_user_model().objects.create_user(username="source-onward-reader")
    values["target"].with_actor(values["actor"]).grant_record_access("viewer", reader)
    assert row.with_actor(reader).has_access("read")
    with actor_context(reader), pytest.raises(PermissionDenied):
        row.with_actor(reader).grant_record_access("viewer", onward)


@pytest.mark.parametrize("existing", [False, True])
def test_read_only_target_access_cannot_retain_or_advance_lineage(evidence, existing):
    retain, values = evidence
    base = retain() if existing else None
    reader = get_user_model().objects.create_user(username="retention-reader")
    values["target"].with_actor(values["actor"]).grant_record_access("viewer", reader)
    assert values["target"].with_actor(reader).has_access("read")
    assert not values["target"].with_actor(reader).has_access("write")
    with pytest.raises(PermissionDenied, match="Write access"):
        retain(actor=reader, base=base, request_key="read-only-revision")
    assert Extraction.objects.count() == int(existing)
    if base is not None:
        assert ExtractionLineage.objects.get(pk=base.lineage_id).head_id == base.pk


def test_actor_scoped_carrier_reads_do_not_join_guarded_sources_around_an_elevated_base_copy(evidence):
    """The reader's carrier and page reads stay in its scope; retaining over its base copies the base as system work."""
    retain, values = evidence
    first = retain(pages=(PageCarrier(source_position=0, page_position=0, width=40, height=60, dpi=100),))
    scoped = Extraction.objects.with_actor(values["actor"]).get(pk=first.pk)
    assert scoped.document_parts()[0].value == values["result"].parts[0].value
    second = retain(base=scoped, request_key="scoped-copy")
    assert scoped.authority_carrier_positions(second.parts.order_by("position")) == {0: 0}
    assert second.pages.get().source_page == 0


def test_schema_error_does_not_disclose_rejected_document_values(evidence):
    retain, values = evidence
    value = deepcopy(values["result"].value)
    value["documents"][0]["title"] = {"private": "rejected document content"}
    with pytest.raises(ValidationError) as raised:
        retain(result=replace(values["result"], value=value))
    assert "rejected document content" not in "".join(format_exception(raised.value))


def test_missing_claim_path_does_not_disclose_document_values_in_tracebacks(evidence):
    retain, values = evidence
    result = replace(values["result"], value={"documents": [], "private": "protected source content"},
                     claims={"/absent": [{"part_position": 0, "start": 0, "end": 4}]})
    with pytest.raises(ValidationError) as raised:
        retain(result=result)
    assert "protected source content" not in "".join(format_exception(raised.value))


def test_nul_in_retained_part_is_rejected_by_the_retention_owner(evidence):
    retain, values = evidence
    part = replace(values["result"].parts[0], value="Rejected\x00text")
    with pytest.raises(ValidationError, match="null characters"):
        retain(result=replace(values["result"], parts=(part,)))
    assert Extraction.objects.count() == 0
