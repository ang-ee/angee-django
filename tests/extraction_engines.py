"""Deterministic engines registered only by test settings."""

from typing import Any, Sequence

from angee.workflows_extraction.engines import (
    DocumentPart,
    DocumentPipelineError,
    DocumentResult,
    DocumentSource,
    ExtractionEngine,
)


class FakeDocumentEngine(ExtractionEngine):
    """Deterministic document-level engine for evidence integration tests."""

    key = "fake_document"
    label = "Fake document extraction"
    pipeline_version = "document-v1"
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
