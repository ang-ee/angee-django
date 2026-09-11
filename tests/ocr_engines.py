"""Deterministic engines registered only by test settings."""

from typing import Any, Sequence

from angee.workflows_ocr.engines import DocumentPart, DocumentResult, DocumentSource, OcrEngine, PageImage, PageResult


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
    document_engine = True

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
