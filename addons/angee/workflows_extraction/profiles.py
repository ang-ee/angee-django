"""Registry-selected, deterministic interpretation of retained evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from angee.base.impl import ImplBase
from angee.base.jsonschema import validate
from angee.workflows_extraction.contracts import DocumentPart, Result, Source


class ExtractionProfile(ImplBase):
    """Pure interpretation selected through ``ANGEE_EXTRACTION_PROFILE_CLASSES``."""

    category = "Extraction"
    label = "Extraction profile"
    evidence_layout: ClassVar[dict[str, Any]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        validate({
            "type": "object",
            "properties": {
                "document_collection": {"type": "string", "format": "json-pointer"},
                "line_collection": {"type": "string", "format": "json-pointer"},
                "root_document_on_missing": {"type": "boolean"},
            },
            "additionalProperties": False,
        }, cls.evidence_layout)

    def detect_carriers(self, source: Source) -> tuple[DocumentPart, ...]:
        """Return format-specific evidence before generic acquisition, without I/O."""
        return ()

    def inference_required(self, result: Mapping[str, Any], unresolved_reasons: Sequence[str]) -> bool:
        """Whether retained unresolved facts require an inference call."""
        return bool(unresolved_reasons)

    def process_parts(
        self,
        sources: Sequence[Source],
        parts: Sequence[DocumentPart],
        schema: dict[str, Any],
        *,
        config: dict[str, Any],
        recognition_used: bool = False,
    ) -> Result:
        """Interpret acquired parts deterministically against the declared schema."""
        raise NotImplementedError

    def normalize_inference_candidate(
        self,
        sources: Sequence[Source],
        parts: Sequence[DocumentPart],
        schema: dict[str, Any],
        *,
        value: dict[str, Any],
        claims: dict[str, list[dict[str, Any]]],
        metadata: dict[str, Any],
        config: dict[str, Any],
        recognition_used: bool = False,
    ) -> Result:
        """Normalize and ground one inference candidate without external I/O."""
        raise NotImplementedError


class UnconfiguredExtractionProfile(ExtractionProfile):
    """Fail closed until a definition selects a registered profile."""

    key = "none"
    label = "No extraction profile"

    def process_parts(self, *args: Any, **kwargs: Any) -> Result:
        raise ValueError("Select a document extraction profile.")

    def normalize_inference_candidate(self, *args: Any, **kwargs: Any) -> Result:
        raise ValueError("Select a document extraction profile.")


# Historical import: the obsolete retry-lineage filtering rule has no successor.
authored_profile_config = dict
