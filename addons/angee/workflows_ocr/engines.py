"""Registry-selected OCR engine contract and deterministic test engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

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


class OcrEngine(ImplBase):
    """Engine protocol selected by an extraction's registry-backed field."""

    category = "OCR"
    label = "OCR engine"

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
            results.get(page_key, config.get("result", {}))
            if isinstance(results, dict)
            else config.get("result", {})
        )
        if not isinstance(value, dict):
            raise ValueError("Fake OCR page results must be JSON objects.")
        return PageResult(value=dict(value))
