"""Immutable, ordered extraction evidence models."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Literal

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.base.impl import ImplClassField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.base.refs import RecordRefMixin
from angee.workflows_extraction.engines import OcrEngine
from angee.workflows_extraction.managers import (
    ExtractionManager, ImmutableEvidenceManager, evidence_insert_allowed,
)


@dataclass(frozen=True, slots=True)
class FactAuthority:
    """Retained provenance classification for one Extraction result fact."""

    kind: Literal["source", "correction", "unverified"]
    decision_id: str = ""


@dataclass(frozen=True, slots=True)
class SourceRef:
    kind: Literal["file", "message_part"]
    public_id: str
    content_digest: str


@dataclass(frozen=True, slots=True)
class PageRef:
    source: SourceRef
    page: int
    carrier_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LineRef:
    identity: str
    selector: str


@dataclass(frozen=True, slots=True)
class DocumentRef:
    identity: str
    selector: str
    lines: tuple[LineRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionRef:
    lineage_key: str
    public_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class CorrectionRef:
    original_extraction_id: str
    original_revision: int
    decision_id: str
    corrected_paths: tuple[str, ...]


class DecisionReadableFile(models.Model):
    """Temporarily let one pending Decision's assignees read its exact File."""

    extends = "storage.File"
    rebac_grantable = {"viewer": "write", "pending_decision": "write"}

    class Meta:
        abstract = True


class ExtractionLineage(AngeeModel):
    """Private lockable head for first allocation and subsequent revision CAS."""

    runtime = True
    key = models.CharField(max_length=64, primary_key=True)
    head = models.ForeignKey(
        "workflows_extraction.Extraction", null=True, on_delete=models.PROTECT, related_name="+"
    )
    objects = ImmutableEvidenceManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not evidence_insert_allowed(kwargs.get("using") or self._state.db):
            raise ValueError("The extraction lineage head changes only during retention.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("The extraction lineage is retained and cannot be deleted.")


class Extraction(SqidMixin, AuditMixin, RecordRefMixin, AngeeModel):
    """One immutable schema-validated claim over an ordered file set."""

    runtime = True
    sqid_prefix = "ext_"
    rebac_grantable = {"viewer": "read", "pending_decision": "read"}

    revision = models.PositiveIntegerField(default=1, editable=False)
    lineage_key = models.CharField(max_length=64, db_index=True, editable=False)
    reuse_key = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(max_length=16, editable=False)
    error_code = models.CharField(max_length=100, blank=True, editable=False)
    schema_id = models.CharField(max_length=255, editable=False)
    schema_digest = models.CharField(max_length=64, editable=False)
    schema = models.JSONField(editable=False)
    engine = ImplClassField(base_class=OcrEngine, registry_setting="ANGEE_OCR_ENGINE_CLASSES", editable=False)
    model = models.ForeignKey(
        "agents.InferenceModel", null=True, blank=True, on_delete=models.PROTECT, related_name="extraction_evidence"
    )
    recognition_model = models.ForeignKey(
        "agents.InferenceModel",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="recognition_extraction_evidence",
    )
    engine_config = models.JSONField(default=dict, blank=True, editable=False)
    result = models.JSONField(editable=False)
    provenance = models.JSONField(default=dict, editable=False)
    document_map = models.JSONField(default=list, editable=False)
    retired_identities = models.JSONField(default=list, editable=False)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=255)
    target = GenericForeignKey("content_type", "object_id")
    objects = ExtractionManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("lineage_key", "-revision")
        rebac_resource_type = "workflows_extraction/extraction"
        constraints = (
            models.UniqueConstraint(fields=("lineage_key", "revision"), name="uniq_extraction_revision"),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Refuse mutation after the evidence row has been inserted."""

        if not evidence_insert_allowed(kwargs.get("using") or self._state.db):
            raise ValueError("Extraction evidence can only be inserted by the retention owner.")
        if self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists():
            raise ValueError("Extraction evidence is immutable; create a new revision.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction evidence is retained and cannot be deleted.")

    def fact_authority(self, pointer: str) -> FactAuthority:
        """Classify retained source or Decision-backed correction provenance."""

        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise ValueError("Extraction fact authority requires a JSON pointer.")
        provenance = self.provenance if isinstance(self.provenance, dict) else {}
        claims = provenance.get("claims")
        if isinstance(claims, dict) and isinstance(claims.get(pointer), list) and claims[pointer]:
            return FactAuthority("source")
        corrections = provenance.get("corrections")
        if not isinstance(corrections, list):
            return FactAuthority("unverified")
        for correction in reversed(corrections):
            if not isinstance(correction, dict) or correction.get("kind") != "human_correction":
                continue
            paths = correction.get("corrected_paths")
            if not isinstance(paths, list):
                continue
            if any(
                isinstance(path, str)
                and (pointer == path or pointer.startswith(f"{path.rstrip('/')}/"))
                for path in paths
            ):
                decision_id = correction.get("decision_id")
                if isinstance(decision_id, str) and decision_id:
                    return FactAuthority("correction", decision_id)
                return FactAuthority("unverified")
        return FactAuthority("unverified")

    @property
    def reference(self) -> ExtractionRef:
        return ExtractionRef(str(self.lineage_key), str(self.sqid), int(self.revision))

    @property
    def document_refs(self) -> tuple[DocumentRef, ...]:
        """Return revision selectors while carrying identities across revisions."""

        return tuple(
            DocumentRef(
                str(item["identity"]), str(item["selector"]),
                tuple(LineRef(str(line["identity"]), str(line["selector"])) for line in item["lines"]),
            )
            for item in self.document_map
        )

    def document_ref(self, identity: str) -> DocumentRef:
        matches = [item for item in self.document_refs if item.identity == identity]
        if len(matches) != 1:
            raise KeyError(identity)
        return matches[0]

    def selected_document(self, identity: str) -> tuple[Mapping[str, Any], DocumentRef]:
        """Resolve an exact retained document without interpreting a printed label."""

        from angee.workflows_extraction.service import json_pointer_value

        reference = self.document_ref(identity)
        result = json_pointer_value(self.result, reference.selector)
        if not isinstance(result, Mapping):
            raise ValueError("The retained document selector does not point to an object.")
        return result, reference

    @property
    def claims(self) -> Mapping[str, Any]:
        return self.provenance.get("claims", {})

    @property
    def corrections(self) -> tuple[CorrectionRef, ...]:
        """Return authority references without exposing the provenance encoding."""

        return tuple(
            CorrectionRef(
                str(item["original_extraction_id"]), int(item["original_extraction_revision"]),
                str(item["decision_id"]), tuple(item.get("corrected_paths", ())),
            )
            for item in self.provenance.get("corrections", ())
        )

    @property
    def stage_provenance(self) -> Mapping[str, Any]:
        return self.provenance.get("document", {})

    @property
    def unresolved_reasons(self) -> tuple[str, ...]:
        return tuple(self.provenance.get("unresolved_reasons", ()))


class ExtractionSource(SqidMixin, AngeeModel):
    """One file's stable position and source identity in an extraction."""

    runtime = True
    sqid_prefix = "exs_"
    extraction = models.ForeignKey("workflows_extraction.Extraction", on_delete=models.CASCADE, related_name="sources")
    file = models.ForeignKey(
        "storage.File", null=True, blank=True, on_delete=models.PROTECT, related_name="extraction_sources"
    )
    message_part = models.ForeignKey(
        "messaging.Part", null=True, blank=True, on_delete=models.PROTECT, related_name="extraction_sources"
    )
    position = models.PositiveIntegerField(editable=False)
    content_hash = models.CharField(max_length=64, editable=False)
    objects = ImmutableEvidenceManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("position",)
        rebac_resource_type = "workflows_extraction/extraction_source"
        constraints = (
            models.UniqueConstraint(fields=("extraction", "position"), name="uniq_extraction_source_position"),
            models.CheckConstraint(
                condition=(
                    models.Q(file__isnull=False, message_part__isnull=True)
                    | models.Q(file__isnull=True, message_part__isnull=False)
                ),
                name="extraction_source_one_input",
            ),
            models.UniqueConstraint(
                fields=("extraction", "file"), condition=models.Q(file__isnull=False), name="uniq_extraction_source_file"
            ),
            models.UniqueConstraint(
                fields=("extraction", "message_part"),
                condition=models.Q(message_part__isnull=False),
                name="uniq_extraction_source_part",
            ),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not evidence_insert_allowed(kwargs.get("using") or self._state.db):
            raise ValueError("Extraction source evidence can only be inserted by the retention owner.")
        if self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists():
            raise ValueError("Extraction source evidence is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction source evidence is retained and cannot be deleted.")

    @property
    def reference(self) -> SourceRef:
        if self.file_id is not None:
            return SourceRef("file", str(self.file.sqid), str(self.content_hash))
        return SourceRef("message_part", str(self.message_part.sqid), str(self.content_hash))


class ExtractionPage(SqidMixin, AngeeModel):
    """One ordered page response retained as extraction evidence."""

    runtime = True
    sqid_prefix = "exp_"
    extraction = models.ForeignKey("workflows_extraction.Extraction", on_delete=models.CASCADE, related_name="pages")
    source = models.ForeignKey("workflows_extraction.ExtractionSource", on_delete=models.CASCADE, related_name="pages")
    position = models.PositiveIntegerField(editable=False)
    source_page = models.PositiveIntegerField(editable=False)
    width = models.PositiveIntegerField(editable=False)
    height = models.PositiveIntegerField(editable=False)
    dpi = models.PositiveIntegerField(editable=False)
    duration_ms = models.PositiveIntegerField(default=0, editable=False)
    result = models.JSONField(editable=False)
    engine_metadata = models.JSONField(default=dict, blank=True, editable=False)
    objects = ImmutableEvidenceManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("position",)
        rebac_resource_type = "workflows_extraction/extraction_page"
        constraints = (
            models.UniqueConstraint(fields=("extraction", "position"), name="uniq_extraction_page_position"),
            models.UniqueConstraint(fields=("source", "source_page"), name="uniq_extraction_source_page"),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not evidence_insert_allowed(kwargs.get("using") or self._state.db):
            raise ValueError("Extraction page evidence can only be inserted by the retention owner.")
        if self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists():
            raise ValueError("Extraction page evidence is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction page evidence is retained and cannot be deleted.")

    @property
    def reference(self) -> PageRef:
        carriers = self.engine_metadata.get("carrier_files", ())
        return PageRef(self.source.reference, int(self.source_page), tuple(str(item) for item in carriers))


class ExtractionPart(SqidMixin, AngeeModel):
    """One ordered raw text or structured artifact behind a final claim."""

    runtime = True
    sqid_prefix = "exr_"
    extraction = models.ForeignKey("workflows_extraction.Extraction", on_delete=models.CASCADE, related_name="parts")
    source = models.ForeignKey("workflows_extraction.ExtractionSource", on_delete=models.CASCADE, related_name="parts")
    position = models.PositiveIntegerField(editable=False)
    source_page = models.PositiveIntegerField(null=True, blank=True, editable=False)
    mime_type = models.CharField(max_length=128, editable=False)
    kind = models.CharField(max_length=32, editable=False)
    method = models.CharField(max_length=128, editable=False)
    content_hash = models.CharField(max_length=64, editable=False)
    width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    dpi = models.PositiveIntegerField(null=True, blank=True, editable=False)
    value = models.JSONField(editable=False)
    claims = models.JSONField(default=dict, blank=True, editable=False)
    metadata = models.JSONField(default=dict, blank=True, editable=False)
    duration_ms = models.PositiveIntegerField(default=0, editable=False)
    objects = ImmutableEvidenceManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("position",)
        rebac_resource_type = "workflows_extraction/extraction_part"
        constraints = (models.UniqueConstraint(fields=("extraction", "position"), name="uniq_extraction_part_position"),)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not evidence_insert_allowed(kwargs.get("using") or self._state.db):
            raise ValueError("Extraction part evidence can only be inserted by the retention owner.")
        if self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists():
            raise ValueError("Extraction part evidence is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction part evidence is retained and cannot be deleted.")
