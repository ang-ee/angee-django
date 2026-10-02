"""Registry-selected, deterministic interpretation of retained evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from jsonpointer import JsonPointer, JsonPointerException
from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

from angee.base.impl import ImplBase
from angee.base.jsonschema import check_schema
from angee.extraction.contracts import DocumentPart, Result, Source
from angee.extraction.enums import ExtractionRole


class EvidenceLayout(BaseModel):
    """Immutable pointers locating logical documents and their lines."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    document_collection: str = ""
    line_collection: str = ""
    root_document_on_missing: StrictBool = False

    @field_validator("document_collection", "line_collection")
    @classmethod
    def valid_pointer(cls, value: str) -> str:
        try:
            JsonPointer(value)
        except JsonPointerException as error:
            raise ValueError("Expected a JSON pointer.") from error
        return value


class ExtractionProfile(ImplBase):
    """Pure interpretation selected through ``ANGEE_EXTRACTION_PROFILE_CLASSES``."""
    registry_setting = "ANGEE_EXTRACTION_PROFILE_CLASSES"

    category = "Extraction"
    label = "Extraction profile"
    evidence_layout: ClassVar[EvidenceLayout] = EvidenceLayout()

    @staticmethod
    def check_schema(schema: dict[str, Any]) -> str:
        """Validate the published object contract and return its stable identifier."""
        schema_id = schema.get("$id") or schema.get("x-version")
        if schema.get("type") != "object" or not isinstance(schema_id, str) or not schema_id:
            raise ValidationError("An extraction schema requires an object type and stable $id or x-version.")
        check_schema(schema)
        return schema_id

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


class PlainTextExtractionProfile(ExtractionProfile):
    """Retain native and recognized text under the shipped plain-text schema."""

    key = "plain_text"
    label = "Plain text"

    def process_parts(
        self, sources: Sequence[Source], parts: Sequence[DocumentPart], schema: dict[str, Any],
        *, config: dict[str, Any], recognition_used: bool = False,
    ) -> Result:
        text_parts = [
            (position, part.value)
            for position, part in enumerate(parts)
            if isinstance(part.value, str) and part.value
        ]
        text = "\n".join(value for _, value in text_parts)
        claims = {
            "/text": [
                {"part_position": position, "start": 0, "end": len(value)}
                for position, value in text_parts
            ]
        } if text_parts else {}
        return Result(
            {"text": text}, tuple(parts), claims,
            (ExtractionRole.RECOGNITION,) if recognition_used else (),
            provider_metadata={"unresolved_reasons": [] if text else ["no_text_carrier"]},
        )

    def normalize_inference_candidate(
        self, sources: Sequence[Source], parts: Sequence[DocumentPart], schema: dict[str, Any],
        *, value: dict[str, Any], claims: dict[str, list[dict[str, Any]]], metadata: dict[str, Any],
        config: dict[str, Any], recognition_used: bool = False,
    ) -> Result:
        return Result(value, tuple(parts), claims, (ExtractionRole.MAPPING,), provider_metadata=metadata)
