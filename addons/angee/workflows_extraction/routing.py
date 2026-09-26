"""Provider-neutral document acquisition helpers."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser

import pypdfium2 as pdfium
from PIL import Image

from angee.workflows_extraction.contracts import (
    DocumentPart,
    DocumentPipelineError,
    DocumentSource,
    ExtractionPartKind,
    PageImage,
)
from angee.workflows_extraction.profiles import ExtractionProfile


@dataclass(frozen=True, slots=True)
class AcquiredDocument:
    """Every ordered page, native evidence, and the recognition subset."""

    parts: tuple[DocumentPart, ...]
    recognition_pages: tuple[PageImage, ...]
    pages: tuple["AcquiredPage", ...]


@dataclass(frozen=True, slots=True)
class AcquiredPage:
    source_position: int
    page_position: int
    native_parts: tuple[DocumentPart, ...]
    recognition_image: PageImage | None = None


def acquire_native_parts(
    sources: Sequence[DocumentSource],
    *,
    profile: ExtractionProfile,
    dpi: int = 200,
    max_edge: int = 3500,
    max_pages: int = 10,
    max_text_bytes: int = 2_000_000,
) -> AcquiredDocument:
    """Prefer structured carriers and retain bounded acquisition failures."""

    try:
        return _acquire_native_parts(
            sources,
            profile=profile,
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
    profile: ExtractionProfile,
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
            carriers = profile.detect_carriers(source)
            if carriers:
                parts.extend(carriers)
                continue
            declared_type = source.mime_type.lower().split(";", 1)[0]
            if declared_type in {"text/plain", "text/html"}:
                text = _decode_declared_text(content)
                if declared_type == "text/html":
                    text = _html_text(text)
                text_bytes = _bounded_text_size(text, text_bytes, max_text_bytes)
                parts.append(_text_part(source, text, method="text_attachment"))
                continue
            if declared_type == "message/rfc822" or (
                declared_type in {"", "application/octet-stream"}
                and source.filename.lower().endswith(".eml")
            ):
                text = _rfc822_text(content)
                text_bytes = _bounded_text_size(text, text_bytes, max_text_bytes)
                parts.append(_text_part(source, text, method="message_attachment"))
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
            if not content:
                raise DocumentPipelineError(
                    "Empty document sources require review.", parts=parts,
                    stage="acquisition", code="empty_source",
                )
            if not declared_type.startswith("image/"):
                raise DocumentPipelineError(
                    "The document source format is not supported for extraction.", parts=parts,
                    stage="acquisition", code="unsupported_media_type",
                )
            page_count += 1
            if page_count > max_pages:
                raise ValueError("The document exceeds its configured page limit.")
            recognition_pages.append(_image_page(source, content, page_position=0, dpi=dpi, max_edge=max_edge))
        except DocumentPipelineError:
            raise
        except (RuntimeError, ValueError) as error:
            raise DocumentPipelineError(
                f"Native document acquisition failed ({type(error).__name__}).", parts=parts,
                stage="acquisition", code=type(error).__name__,
            ) from None
    page_parts: dict[tuple[int, int], list[DocumentPart]] = {}
    page_images = {(page.source_position, page.page_position): page for page in recognition_pages}
    for part in parts:
        page_parts.setdefault((part.source_position, part.source_page or 0), []).append(part)
    keys = sorted(set(page_parts) | set(page_images))
    return AcquiredDocument(
        tuple(parts), tuple(recognition_pages),
        tuple(
            AcquiredPage(source_position, page_position, tuple(page_parts.get((source_position, page_position), ())),
                         page_images.get((source_position, page_position)))
            for source_position, page_position in keys
        ),
    )


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


def _rfc822_text(content: bytes) -> str:
    """Extract inert subject/body text from one retained RFC 822 attachment."""

    message = BytesParser(policy=policy.default).parsebytes(content)
    body = message.get_body(preferencelist=("plain", "html"))
    if body is None:
        value = message.get_content()
        text = value if isinstance(value, str) else ""
        media_type = message.get_content_type()
    else:
        value = body.get_content()
        text = value if isinstance(value, str) else ""
        media_type = body.get_content_type()
    if media_type == "text/html":
        text = _html_text(text)
    subject = str(message.get("subject") or "").strip()
    return "\n".join(value for value in (subject, text.strip()) if value)


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
        ExtractionPartKind.NATIVE_TEXT,
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
