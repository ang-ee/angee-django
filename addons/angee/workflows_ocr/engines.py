"""Registry-selected OCR engine contract and deterministic test engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Sequence

from angee.base.impl import ImplBase


@dataclass(frozen=True, slots=True)
class PageImage:
    """One bounded raster page supplied to an OCR engine."""

    source_position: int
    page_position: int
    mime_type: str
    image_bytes: bytes
    width: int
    height: int
    dpi: int


@dataclass(frozen=True, slots=True)
class PageResult:
    """One page's validated engine response and non-sensitive metrics."""

    value: dict[str, Any]
    duration_ms: int = 0
    engine_metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    text: str
    duration_ms: int = 0
    engine_metadata: dict[str, Any] | None = None


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
    kind: Literal["structured", "native_text", "recognized_text"]
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
    engine_metadata: dict[str, Any] | None = None


class DocumentPipelineError(RuntimeError):
    """Bounded pipeline failure carrying only already acquired raw evidence."""

    def __init__(self, message: str, *, parts: Sequence[DocumentPart] = ()) -> None:
        super().__init__(message)
        self.parts = tuple(parts)


class OcrEngine(ImplBase):
    """Engine protocol selected by an extraction's registry-backed field."""

    category = "OCR"
    label = "OCR engine"
    pipeline_version: ClassVar[str] = "page-v1"

    def extract_document(
        self,
        sources: Sequence[DocumentSource],
        schema: dict[str, Any],
        *,
        model: Any | None,
        recognition_model: Any | None,
        config: dict[str, Any],
        timeout: float,
    ) -> DocumentResult:
        """Extract a whole document; engines opt in without replacing the registry."""

        raise NotImplementedError

    def extract_page(
        self,
        page: PageImage,
        schema: dict[str, Any],
        *,
        model: Any,
        config: dict[str, Any],
        timeout: float,
    ) -> PageResult:
        raise NotImplementedError

    def recognize_page(
        self, page: PageImage, *, model: Any, config: dict[str, Any], timeout: float
    ) -> RecognitionResult:
        """Recognize printed text without interpreting it as business facts."""

        raise NotImplementedError


class FakeOcrEngine(OcrEngine):
    """Deterministic engine for workflow and consumer integration tests."""

    key = "fake"
    label = "Fake OCR"

    def extract_page(
        self,
        page: PageImage,
        schema: dict[str, Any],
        *,
        model: Any,
        config: dict[str, Any],
        timeout: float,
    ) -> PageResult:
        del schema, model, timeout
        results = config.get("page_results", {})
        page_key = f"{page.source_position}:{page.page_position}"
        value = (
            results.get(page_key, config.get("result", {})) if isinstance(results, dict) else config.get("result", {})
        )
        if not isinstance(value, dict):
            raise ValueError("Fake OCR page results must be JSON objects.")
        return PageResult(value=dict(value))


class FakeDocumentEngine(OcrEngine):
    """Deterministic document-level engine for evidence integration tests."""

    key = "fake_document"
    label = "Fake document extraction"
    pipeline_version = "document-v1"

    def extract_document(
        self,
        sources: Sequence[DocumentSource],
        schema: dict[str, Any],
        *,
        model: Any | None,
        recognition_model: Any | None,
        config: dict[str, Any],
        timeout: float,
    ) -> DocumentResult:
        del schema, model, recognition_model, timeout
        value = config.get("result", {})
        if config.get("nul_result"):
            value = {"number": "SYN\x00BAD", "rows": []}
        if not isinstance(value, dict):
            raise ValueError("Fake document result must be an object.")
        parts = tuple(
            DocumentPart(
                source.source_position,
                None,
                source.mime_type,
                "native_text" if isinstance(source.content, str) else "structured",
                str(config.get("source_text") or "Synthetic invoice evidence")
                if isinstance(source.content, bytes)
                else source.content,
                "fake",
                source.content_hash,
            )
            for source in sources
        )
        claims = {f"/{key}": [{"part_position": 0}] for key in value} if parts else {}
        return DocumentResult(dict(value), parts, claims, engine_metadata={"route": "fake"})
