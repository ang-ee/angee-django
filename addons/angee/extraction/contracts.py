"""Immutable values shared by extraction profiles, inference and retained evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from django.db.models import TextChoices
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from angee.base.evidence import EvidenceReference
from angee.base.identity import public_id_of
from angee.extraction.enums import ExtractionRole, ExtractionSourceKind


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Public identity and digest of one immutable evidence source."""

    kind: ExtractionSourceKind
    public_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class LineRef:
    """Stable line identity and its current result pointer."""

    identity: str
    selector: str


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """Stable document identity with its current lines and result pointer."""

    identity: str
    selector: str
    lines: tuple[LineRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionRef:
    """One retained revision in its immutable lineage."""

    lineage_id: int
    public_id: str
    revision: int


class _Outcome(BaseModel):
    """Facts shared by the two exclusive retained revision outcomes."""

    model_config = ConfigDict(extra="forbid")
    claims: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    used_model_roles: list[ExtractionRole] = Field(default_factory=list)
    unresolved_reasons: list[str] = Field(default_factory=list)
    document: dict[str, Any] = Field(default_factory=dict)
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    request_digest: str
    identity_correspondence: dict[str, Any] | None = None


class SucceededOutcome(_Outcome):
    """A schema-valid retained candidate."""

    kind: Literal["succeeded"] = "succeeded"


class FailedOutcome(_Outcome):
    """A retained candidate with an explicit failure code and stage."""

    kind: Literal["failed"] = "failed"
    code: str = Field(min_length=1)
    stage: str | None = None


ExtractionOutcome = Annotated[SucceededOutcome | FailedOutcome, Field(discriminator="kind")]
outcome_adapter: TypeAdapter[SucceededOutcome | FailedOutcome] = TypeAdapter(ExtractionOutcome)


@dataclass(frozen=True, slots=True)
class CorrectionRef:
    """Authority over scalar facts changed or explicitly confirmed."""

    original_extraction_id: str
    original_revision: int
    decision_id: str
    corrected_paths: tuple[str, ...]
    revision_parent_extraction_id: str | None = None
    revision_parent_revision: int | None = None


@dataclass(frozen=True, slots=True)
class CorrectionBinding:
    """Frozen fact authority and exact revision parent for one correction."""

    authority: ExtractionRef
    revision_parent: ExtractionRef

    @property
    def bridges_revision_parent(self) -> bool:
        """Whether the decision authority predates the revision being replaced."""
        return self.authority.public_id != self.revision_parent.public_id

    def payload(self) -> dict[str, Any]:
        """Return the authority references captured with the revision."""
        return {
            "authority_extraction_id": self.authority.public_id,
            "authority_extraction_revision": self.authority.revision,
            "revision_parent_extraction_id": self.revision_parent.public_id,
            "revision_parent_extraction_revision": self.revision_parent.revision,
        }


class ExtractionPartKind(TextChoices):
    """Closed carrier kinds retained for an extraction part."""

    STRUCTURED = "structured", "Structured"
    NATIVE_TEXT = "native_text", "Native text"
    RECOGNIZED_TEXT = "recognized_text", "Recognized text"


@dataclass(frozen=True, slots=True)
class PageImage:
    """One bounded raster page supplied for text recognition."""

    source_position: int
    page_position: int
    mime_type: str
    image_bytes: bytes
    width: int
    height: int
    dpi: int


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """Recognized text, retained telemetry and this invocation's usage."""

    text: str
    duration_ms: int = 0
    provider_metadata: dict[str, Any] | None = None
    usage_delta: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MappingResult:
    """One mapped candidate with retained telemetry and invocation usage."""

    value: dict[str, Any]
    claims: dict[str, list[dict[str, Any]]]
    provider_metadata: dict[str, Any]
    usage_delta: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Source:
    """One authorized, content-addressed document input."""

    source_position: int
    content_hash: str
    mime_type: str
    content: bytes | str
    file: Any | None = None
    message_part: Any | None = None

    @property
    def record(self) -> Any:
        """Return the source record whose standing read permission admits this input."""
        return self.file if self.file is not None else self.message_part

    @property
    def evidence_reference(self) -> EvidenceReference:
        """Supply this input's public record identity to the base admission check."""
        row = self.record
        return EvidenceReference(model=row._meta.label, id=public_id_of(row))

    @property
    def filename(self) -> str:
        """Retained file name, or an empty string for message fragments."""
        return str(self.file.filename) if self.file is not None else ""


@dataclass(frozen=True, slots=True)
class DocumentPart:
    """Immutable evidence produced by one acquisition tier."""

    source_position: int
    source_page: int | None
    mime_type: str
    kind: ExtractionPartKind
    value: dict[str, Any] | str
    method: str
    content_hash: str
    width: int | None = None
    height: int | None = None
    dpi: int | None = None
    duration_ms: int = 0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Result:
    """A schema candidate, acquired evidence and grounded scalar claims."""

    value: dict[str, Any]
    parts: tuple[DocumentPart, ...]
    claims: dict[str, list[dict[str, Any]]]
    used_model_roles: tuple[ExtractionRole, ...] = ()
    duration_ms: int = 0
    provider_metadata: dict[str, Any] | None = None


class PipelineError(RuntimeError):
    """Bounded failure retaining acquired evidence and invocation usage."""

    def __init__(
        self,
        message: str,
        *,
        parts: Sequence[DocumentPart] = (),
        stage: str = "",
        code: str = "",
        metadata: dict[str, Any] | None = None,
        usage_delta: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.parts = tuple(parts)
        self.stage = stage
        self.code = code
        self.metadata = dict(metadata or {})
        self.usage_delta = dict(usage_delta or {})
