"""Immutable values shared by extraction profiles, providers and retained evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from django.db.models import TextChoices


@dataclass(frozen=True, slots=True)
class FactAuthority:
    """Provenance classification for one retained result fact."""

    kind: Literal["source", "correction", "unverified"]
    decision_id: str = ""


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Public identity and digest of one immutable evidence source."""

    kind: Literal["file", "message_part"]
    public_id: str
    content_digest: str


@dataclass(frozen=True, slots=True)
class PageRef:
    """One source page and the carriers acquired from it."""

    source: SourceRef
    page: int
    carrier_ids: tuple[str, ...] = ()


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

    lineage_key: str
    public_id: str
    revision: int


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


class ExtractionPartKind(TextChoices, StrEnum):
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
    used_model_roles: tuple[Literal["mapping", "recognition"], ...] = ()
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


@dataclass(frozen=True, slots=True)
class PageResult:
    """One page's validated provider response and non-sensitive metrics."""

    value: dict[str, Any]
    duration_ms: int = 0
    provider_metadata: dict[str, Any] | None = None


# Historical consumer imports name the same contracts, not parallel types.
DocumentSource = Source
DocumentResult = Result
DocumentPipelineError = PipelineError
