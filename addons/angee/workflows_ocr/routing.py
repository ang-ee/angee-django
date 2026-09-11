"""Provider-neutral acquisition and local schema-mapping helpers."""

from __future__ import annotations

import hashlib
import io
import re
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

import pypdfium2 as pdfium
from PIL import Image

from angee.workflows_ocr.engines import (
    DocumentPart,
    DocumentPipelineError,
    DocumentSource,
    OcrEngine,
    PageImage,
)
from angee.workflows_ocr.structured import extract_structured_sources

_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_NUMBER_TOKEN = re.compile(r"(?<![\w./-])[-+]?\d+(?:[.,]\d+)?(?![\w./-])")


@dataclass(frozen=True, slots=True)
class AcquiredDocument:
    """Native evidence plus only the pages that still require recognition."""

    parts: tuple[DocumentPart, ...]
    recognition_pages: tuple[PageImage, ...]


def acquire_native_parts(
    sources: Sequence[DocumentSource],
    *,
    dpi: int = 200,
    max_edge: int = 3500,
    max_pages: int = 10,
    max_text_bytes: int = 2_000_000,
) -> AcquiredDocument:
    """Prefer structured carriers and retain bounded acquisition failures."""

    try:
        return _acquire_native_parts(
            sources,
            dpi=dpi,
            max_edge=max_edge,
            max_pages=max_pages,
            max_text_bytes=max_text_bytes,
        )
    except DocumentPipelineError:
        raise
    except (RuntimeError, ValueError) as error:
        raise DocumentPipelineError(f"Native document acquisition failed ({type(error).__name__}).") from None


def _acquire_native_parts(
    sources: Sequence[DocumentSource],
    *,
    dpi: int,
    max_edge: int,
    max_pages: int,
    max_text_bytes: int,
) -> AcquiredDocument:
    """Acquire structured carriers and text, rasterising only text-poor pages."""

    parts: list[DocumentPart] = []
    recognition_pages: list[PageImage] = []
    text_bytes = 0
    page_count = 0
    for source in sources:
        try:
            if source.message_part is not None:
                text = _message_text(source)
                text_bytes = _bounded_text_size(text, text_bytes, max_text_bytes)
                parts.append(_text_part(source, text, method="message_fragment"))
                continue
            content = source.content
            if not isinstance(content, bytes):
                raise ValueError("File sources require a byte snapshot.")
            filename = str(getattr(source.file, "filename", "") or getattr(source.file, "name", ""))
            structured = extract_structured_sources(
                content, media_type=source.mime_type, filename=filename, source_position=source.source_position
            )
            if structured:
                for item in structured:
                    value = {
                        "facts": [asdict(fact) for fact in item.facts],
                        "evidence": asdict(item.evidence),
                        "review_reasons": list(item.review_reasons),
                        "carrier": item.carrier,
                        "name": item.name,
                    }
                    parts.append(
                        DocumentPart(
                            source.source_position,
                            None,
                            item.media_type,
                            "structured",
                            value,
                            f"structured:{item.kind}",
                            item.content_sha256,
                        )
                    )
                continue
            declared_type = source.mime_type.lower().split(";", 1)[0]
            if declared_type in {"text/plain", "text/html"}:
                text = _decode_declared_text(content)
                if declared_type == "text/html":
                    text = _html_text(text)
                text_bytes = _bounded_text_size(text, text_bytes, max_text_bytes)
                parts.append(_text_part(source, text, method="text_attachment"))
                continue
            if source.mime_type == "application/pdf":
                native, scanned = _pdf_parts(
                    source, content, dpi=dpi, max_edge=max_edge, max_pages=max_pages - page_count
                )
                page_count += len(native) + len(scanned)
                if page_count > max_pages:
                    raise ValueError("The document exceeds its configured page limit.")
                for part in native:
                    text_bytes = _bounded_text_size(str(part.value), text_bytes, max_text_bytes)
                parts.extend(native)
                recognition_pages.extend(scanned)
                continue
            page_count += 1
            if page_count > max_pages:
                raise ValueError("The document exceeds its configured page limit.")
            recognition_pages.append(_image_page(source, content, page_position=0, dpi=dpi, max_edge=max_edge))
        except DocumentPipelineError:
            raise
        except (RuntimeError, ValueError) as error:
            raise DocumentPipelineError(
                f"Native document acquisition failed ({type(error).__name__}).", parts=parts
            ) from None
    return AcquiredDocument(tuple(parts), tuple(recognition_pages))


def recognize_pages(
    pages: Sequence[PageImage],
    *,
    engine: OcrEngine,
    model: Any | None,
    config: dict[str, Any],
    timeout: float,
    acquired_parts: Sequence[DocumentPart] = (),
) -> tuple[DocumentPart, ...]:
    """Recognize exactly the supplied scanned pages and retain their plain text."""

    if pages and model is None:
        raise DocumentPipelineError("Scanned pages require a recognition model.", parts=acquired_parts)
    started = time.monotonic()
    parts = []
    for page in pages:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise DocumentPipelineError("Text recognition timed out.", parts=(*acquired_parts, *parts))
        try:
            result = engine.recognize_page(page, model=model, config=config, timeout=remaining)
        except (RuntimeError, TimeoutError, ValueError) as error:
            raise DocumentPipelineError(
                f"Text recognition failed ({type(error).__name__}).", parts=(*acquired_parts, *parts)
            ) from None
        text = result.text.strip()
        parts.append(
            DocumentPart(
                page.source_position,
                page.page_position,
                "text/plain",
                "recognized_text",
                text,
                f"{engine.key}:text_recognition",
                hashlib.sha256(text.encode()).hexdigest(),
                page.width,
                page.height,
                page.dpi,
                result.duration_ms,
                result.engine_metadata,
            )
        )
    return tuple(parts)


def derive_text_claims(value: Any, parts: Sequence[DocumentPart]) -> dict[str, list[dict[str, Any]]]:
    """Derive exact scalar spans from retained text; model output never supplies provenance."""

    claims: dict[str, list[dict[str, Any]]] = {}

    def visit(item: Any, pointer: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{pointer}/{index}")
        elif item not in (None, "") and not isinstance(item, bool):
            needle = str(item)
            matches = []
            for position, part in enumerate(parts):
                if not isinstance(part.value, str):
                    continue
                span = _grounded_span(needle, part.value)
                if span is not None:
                    matches.append({"part_position": position, "start": span[0], "end": span[1]})
            if matches:
                claims[pointer or "/"] = matches

    visit(value, "")
    return claims


def _grounded_span(needle: str, evidence: str) -> tuple[int, int] | None:
    if not _NUMBER.fullmatch(needle):
        start = evidence.find(needle)
        return (start, start + len(needle)) if start >= 0 else None
    for match in _NUMBER_TOKEN.finditer(evidence):
        candidate = match.group()
        if candidate == needle or _decimal_equivalent(needle, candidate):
            return match.span()
    return None


def _decimal_equivalent(left: str, right: str) -> bool:
    if not ({".", ","} & set(left)) or not ({".", ","} & set(right)):
        return False
    try:
        return Decimal(left.replace(",", ".")) == Decimal(right.replace(",", "."))
    except InvalidOperation:
        return False


def _message_text(source: DocumentSource) -> str:
    text = str(source.content)
    if source.mime_type.lower().split(";", 1)[0] != "text/html":
        return text
    return _html_text(text)


def _html_text(text: str) -> str:
    parser = _InertHtmlText()
    parser.feed(text)
    parser.close()
    return parser.text


def _decode_declared_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Declared text extraction sources must be UTF-8 encoded.") from error


class _InertHtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in {"script", "style"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self._chunks.append(data.strip())

    @property
    def text(self) -> str:
        return "\n".join(self._chunks)


def _bounded_text_size(text: str, current: int, maximum: int) -> int:
    if "\x00" in text:
        raise ValueError("Native document text contains unsupported null characters.")
    total = current + len(text.encode())
    if total > maximum:
        raise ValueError("Native document text exceeds its configured byte limit.")
    return total


def _text_part(source: DocumentSource, text: str, *, method: str, page: int | None = None) -> DocumentPart:
    return DocumentPart(
        source.source_position,
        page,
        "text/plain",
        "native_text",
        text,
        method,
        hashlib.sha256(text.encode()).hexdigest(),
    )


def _pdf_parts(
    source: DocumentSource,
    content: bytes,
    *,
    dpi: int,
    max_edge: int,
    max_pages: int,
) -> tuple[tuple[DocumentPart, ...], tuple[PageImage, ...]]:
    document = pdfium.PdfDocument(content)
    native: list[DocumentPart] = []
    scanned: list[PageImage] = []
    try:
        if len(document) > max_pages:
            raise ValueError("The document exceeds its configured page limit.")
        for position in range(len(document)):
            page = document[position]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range().strip()
                finally:
                    textpage.close()
                if text:
                    native.append(_text_part(source, text, method="pdf_text", page=position))
                else:
                    image = page.render(scale=dpi / 72).to_pil()
                    scanned.append(_pil_page(source, image, page_position=position, dpi=dpi, max_edge=max_edge))
            finally:
                page.close()
    finally:
        document.close()
    return tuple(native), tuple(scanned)


def _image_page(source: DocumentSource, content: bytes, *, page_position: int, dpi: int, max_edge: int) -> PageImage:
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except Exception as error:
        raise ValueError("Extraction source is not a supported document or image.") from error
    return _pil_page(source, image, page_position=page_position, dpi=dpi, max_edge=max_edge)


def _pil_page(source: DocumentSource, image: Image.Image, *, page_position: int, dpi: int, max_edge: int) -> PageImage:
    image.thumbnail((max_edge, max_edge))
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=90)
    return PageImage(
        source.source_position, page_position, "image/jpeg", output.getvalue(), image.width, image.height, dpi
    )
