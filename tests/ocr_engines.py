"""Deterministic engines registered only by test settings."""

from typing import Any, Sequence

from angee.workflows_extraction.engines import (
    DocumentPart,
    DocumentPipelineError,
    DocumentResult,
    DocumentSource,
    OcrEngine,
    PageImage,
    PageResult,
)


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
    evidence_layout = {"line_collection": "/rows"}

    def process_parts(
        self,
        sources: Sequence[DocumentSource],
        parts: Sequence[DocumentPart],
        schema: dict[str, Any],
        *,
        config: dict[str, Any],
        recognition_used: bool = False,
    ) -> DocumentResult:
        """Pure current-pipeline fixture; acquired parts remain the evidence owner."""

        del sources, schema, recognition_used
        if config.get("failure"):
            raise DocumentPipelineError(
                "Synthetic deterministic profile failure.",
                parts=parts,
                stage="processing",
                code="SyntheticFailure",
            )
        value = config.get("result")
        page_results = config.get("page_results")
        if value is None and isinstance(page_results, dict):
            ordered = [page_results[key] for key in sorted(page_results)]
            value = dict(ordered[0]) if ordered else {}
            if ordered:
                value["rows"] = [row for item in ordered for row in item.get("rows", [])]
        value = value or {}
        if config.get("nul_result"):
            value = {"number": "SYN\x00BAD", "rows": []}
        if not isinstance(value, dict):
            raise ValueError("Fake document result must be an object.")
        claims: dict[str, list[dict[str, Any]]] = {}
        if parts:
            for key, item in value.items():
                if isinstance(item, list):
                    claims.update({
                        f"/{key}/{index}": [{"part_position": 0}]
                        for index in range(len(item))
                    })
                else:
                    claims[f"/{key}"] = [{"part_position": 0}]
        return DocumentResult(
            dict(value), tuple(parts), claims, engine_metadata={"route": "fake"},
        )

    def normalize_inference_candidate(
        self,
        sources: Sequence[DocumentSource],
        parts: Sequence[DocumentPart],
        schema: dict[str, Any],
        *,
        value: dict[str, Any],
        claims: dict[str, list[dict[str, Any]]],
        metadata: dict[str, Any],
        config: dict[str, Any],
        recognition_used: bool = False,
    ) -> DocumentResult:
        del sources, schema, config, recognition_used
        return DocumentResult(
            dict(value), tuple(parts), dict(claims), ("mapping",),
            engine_metadata=dict(metadata),
        )

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
