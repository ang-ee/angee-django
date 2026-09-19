"""Pure value contracts for retained extraction evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


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
