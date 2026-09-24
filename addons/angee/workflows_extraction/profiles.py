"""Registry-selected domain interpretation of retained extraction evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from angee.base.impl import ImplBase
from angee.workflows_extraction.contracts import DocumentPart, DocumentResult, DocumentSource


class ExtractionProfile(ImplBase):
    """Domain interpretation selected by an extraction's registry-backed field."""

    category = "Extraction"
    label = "Extraction profile"
    pipeline_version: ClassVar[str] = "page-v1"
    evidence_layout: ClassVar[dict[str, Any]] = {}

    def detect_carriers(self, source: DocumentSource) -> tuple[DocumentPart, ...]:
        """Return domain-native evidence, or no parts for generic acquisition.

        Called for byte-backed files before text/image routing. Implementations
        own format detection, bounded parsing and field interpretation. They must
        use only the retained source snapshot and perform no external I/O.
        """

        return ()

    def inference_required(self, result: Mapping[str, Any], unresolved_reasons: Sequence[str]) -> bool:
        """Return whether retained unresolved facts require another model call.

        Domain profiles may exclude review reasons that belong to later business
        controls.  The shared workflow still retains those reasons and forwards
        them unchanged; this hook only owns whether mapping inference is needed.
        """

        del result
        return bool(unresolved_reasons)

    def process_parts(
        self,
        sources: Sequence[DocumentSource],
        parts: Sequence[DocumentPart],
        schema: dict[str, Any],
        *,
        config: dict[str, Any],
        recognition_used: bool = False,
    ) -> DocumentResult:
        """Published domain profile's pure deterministic processing contract."""

        raise NotImplementedError

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
        """Pure domain meaning and grounding of one bound mapping response."""

        raise NotImplementedError


class UnconfiguredExtractionProfile(ExtractionProfile):
    """Fail closed until a publication selects a domain profile."""

    key = "none"
    label = "No extraction profile"

    def process_parts(self, *args: Any, **kwargs: Any) -> DocumentResult:
        raise ValueError("Select a document extraction profile.")

    def normalize_inference_candidate(self, *args: Any, **kwargs: Any) -> DocumentResult:
        raise ValueError("Select a document extraction profile.")
