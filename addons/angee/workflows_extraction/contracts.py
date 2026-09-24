"""Pure value contracts for retained extraction evidence and routing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from django.db.models import TextChoices


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
    """Decision authority over scalar facts changed or explicitly confirmed."""

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
        return self.authority.public_id != self.revision_parent.public_id

    def payload(self) -> dict[str, Any]:
        return {
            "authority_extraction_id": self.authority.public_id,
            "authority_extraction_revision": self.authority.revision,
            "revision_parent_extraction_id": self.revision_parent.public_id,
            "revision_parent_extraction_revision": self.revision_parent.revision,
        }


class ExtractionPartKind(TextChoices, StrEnum):
    """Closed carrier kind retained for one extraction part."""

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
    """Recognized text, retained telemetry, and this invocation's usage."""

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
class DocumentSource:
    """One authorized, content-addressed document input."""

    source_position: int
    content_hash: str
    mime_type: str
    content: bytes | str
    file: Any | None = None
    message_part: Any | None = None


@dataclass(frozen=True, slots=True)
class DocumentPart:
    """Raw immutable evidence produced by one document pipeline tier."""

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
class DocumentResult:
    """A final schema candidate plus its retained raw evidence and claims."""

    value: dict[str, Any]
    parts: tuple[DocumentPart, ...]
    claims: dict[str, list[dict[str, Any]]]
    used_model_roles: tuple[Literal["mapping", "recognition"], ...] = ()
    duration_ms: int = 0
    provider_metadata: dict[str, Any] | None = None


class DocumentPipelineError(RuntimeError):
    """Bounded failure with acquired evidence and this invocation's usage."""

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


@dataclass(frozen=True, slots=True)
class PageResult:
    """One page's validated provider response and non-sensitive metrics."""

    value: dict[str, Any]
    duration_ms: int = 0
    provider_metadata: dict[str, Any] | None = None
