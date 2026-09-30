"""Bounded document acquisition and protected carrier snapshots."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import asdict
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Any, cast

import pypdfium2 as pdfium
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai.settings import ThinkingLevel
from rebac import to_subject_ref

from angee.agents.backends import InferenceBackend
from angee.base.actors import actor_user_id
from angee.base.impl import ImplBase
from angee.extraction.contracts import (
    DocumentPart,
    ExtractionPartKind,
    PageImage,
    Source,
)
from angee.extraction.enums import ExtractionSourceKind
from angee.extraction.profiles import ExtractionProfile
from angee.storage.models import UploadState


class ExtractionRequestConfig(BaseModel):
    """The bounded model settings an extraction definition may author."""

    model_config = ConfigDict(extra="forbid")
    timeout: float = Field(default=120, gt=0, le=240)
    max_tokens: int = Field(default=8192, gt=0, le=32768)
    temperature: float = Field(default=0, ge=0, le=2)
    thinking: ThinkingLevel | None = None

    @field_validator("timeout")
    @classmethod
    def valid_timeout(cls, value: float) -> float:
        InferenceBackend.validate_timeout(value)
        return value


class ExtractionConfig(BaseModel):
    """Bounded page acquisition settings."""

    model_config = ConfigDict(extra="forbid")
    max_pages: int = Field(default=10, ge=1, le=100)
    max_text_bytes: int = Field(default=2_000_000, ge=1, le=2_000_000)
    dpi: int = Field(default=200, ge=36, le=600)
    max_edge: int = Field(default=3500, ge=1, le=3500)


class PartCarrier(BaseModel):
    """A protected acquisition envelope; workflow rows retain no document values."""

    model_config = ConfigDict(extra="forbid")
    source_position: int = Field(ge=0)
    source_page: int | None = Field(default=None, ge=0)
    kind: ExtractionPartKind
    file_id: str
    content_hash: str

    @classmethod
    def retain(cls, part: DocumentPart, *, actor: Any, drive_id: str = "") -> PartCarrier:
        """Retain text and structured values under the storage owner's permissions."""
        file = apps.get_model("storage.File").objects.ingest_bytes(
            json.dumps(asdict(part), ensure_ascii=False).encode(),
            filename="extraction-part.json",
            owner_id=actor_user_id(to_subject_ref(actor)),
            drive_id=drive_id,
        )
        return cls(source_position=part.source_position, source_page=part.source_page, kind=part.kind,
                   file_id=str(file.sqid), content_hash=str(file.content_hash))

    def restore(self, ctx: Any) -> DocumentPart:
        """Read the envelope only after rechecking its file authority and digest."""
        file = ctx.load(apps.get_model("storage.File"), self.file_id)
        content = file.read_verified(max_bytes=settings.ANGEE_EXTRACTION_MAX_BYTES, expected_digest=self.content_hash)
        part = ImplBase.parse_value(json.loads(content), DocumentPart, "part")
        if (part.source_position, part.source_page, part.kind) != (self.source_position, self.source_page, self.kind):
            raise ValidationError("The retained part identity changed.")
        return part


class SourceSnapshot(BaseModel):
    """Frozen source identity; bytes remain with their storage owner."""

    model_config = ConfigDict(extra="forbid")
    kind: ExtractionSourceKind
    public_id: str
    content_hash: str
    mime_type: str

    def restore(self, ctx: Any, position: int) -> Source:
        """Recheck actor access and source identity without reading storage again."""
        model = apps.get_model("storage.File" if self.kind == ExtractionSourceKind.FILE else "messaging.Part")
        row = ctx.load(model, self.public_id)
        if self.kind == ExtractionSourceKind.MESSAGE_PART and row.fragment is None:
            raise ValidationError("The prepared text fragment is unavailable.")
        digest = row.content_hash if self.kind == ExtractionSourceKind.FILE else row.fragment.hash
        if str(digest) != self.content_hash or (
            self.kind == ExtractionSourceKind.FILE and row.upload_state != UploadState.READY
        ):
            raise ValidationError("The prepared source identity changed.")
        content = b"" if self.kind == ExtractionSourceKind.FILE else str(row.fragment.text)
        return Source(position, self.content_hash, self.mime_type, content, **{self.kind: row})


class PageCarrier(BaseModel):
    """One prepared page with an optional retained raster requiring recognition."""

    model_config = ConfigDict(extra="forbid")
    source_position: int = Field(ge=0)
    page_position: int = Field(ge=0)
    image_file_id: str = ""
    image_digest: str = ""
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    dpi: int = Field(default=200, gt=0)

    def image(self, file: Any) -> PageImage:
        """Read and verify the retained raster once before a model call."""
        content = file.read_verified(max_bytes=settings.ANGEE_EXTRACTION_MAX_BYTES, expected_digest=self.image_digest)
        return PageImage(
            self.source_position, self.page_position, "image/jpeg", content, self.width, self.height, self.dpi
        )


class PreparedDocument(BaseModel):
    """A single preparation snapshot; downstream steps never repeat acquisition."""

    model_config = ConfigDict(extra="forbid")
    sources: list[SourceSnapshot]
    pages: list[PageCarrier]
    parts: list[PartCarrier]
    hold_reasons: list[str] = Field(default_factory=list)

    @property
    def recognition_pages(self) -> list[PageCarrier]:
        """Return the prepared pages whose retained rasters need recognition."""
        return [page for page in self.pages if page.image_file_id]


class RecognitionOutput(BaseModel):
    """Recognized text bound to the exact prepared raster identity."""

    model_config = ConfigDict(extra="forbid")
    page: PageCarrier
    part: PartCarrier


def prepare_pages(
    files: Sequence[Any],
    message_parts: Sequence[Any],
    *,
    profile: ExtractionProfile,
    config: ExtractionConfig,
    actor: Any,
    heartbeat: Callable[[], None],
) -> PreparedDocument:
    """Read verified sources, prefer native text, and rasterize only missing text."""
    snapshots: list[SourceSnapshot] = []
    parts: list[DocumentPart] = []
    pages: list[PageCarrier] = []
    holds: list[str] = []
    limit, text_limit, dpi, max_edge = config.max_pages, config.max_text_bytes, config.dpi, config.max_edge
    for position, row in enumerate([*files, *message_parts]):
        heartbeat()
        is_file = position < len(files)
        if is_file:
            content = row.read_verified(max_bytes=settings.ANGEE_EXTRACTION_MAX_BYTES)
            digest, mime = str(row.content_hash), str(row.mime_type.mime_type)
        else:
            if row.fragment is None:
                raise ValidationError("A message source requires a retained text fragment.")
            content = str(row.fragment.text).encode()
            digest, mime = str(row.fragment.hash), str(row.type)
        if not is_file and hashlib.sha256(content).hexdigest() != digest:
            raise ValidationError("Document source bytes changed.")
        kind = ExtractionSourceKind.FILE if is_file else ExtractionSourceKind.MESSAGE_PART
        snapshots.append(SourceSnapshot(kind=kind, public_id=str(row.sqid), content_hash=digest, mime_type=mime))
        source = Source(position, digest, mime, content if is_file else content.decode(), **{kind: row})
        detected = profile.detect_carriers(source) if is_file else ()
        if detected:
            parts.extend(detected)
            pages.extend(
                PageCarrier(source_position=position, page_position=page)
                for page in sorted({p.source_page or 0 for p in detected})
            )
        elif mime in {"text/plain", "text/html", "message/rfc822"} or not is_file:
            text = content.decode("utf-8-sig")
            if mime == "message/rfc822":
                message = BytesParser(policy=policy.default).parsebytes(content)
                body = message.get_body(preferencelist=("plain", "html"))
                text = str(body.get_content()) if body else ""
                mime = body.get_content_type() if body else "text/plain"
            if mime == "text/html":
                parser = _Text()
                parser.feed(text)
                text = "\n".join(parser.chunks)
            parts.append(_text_part(position, 0, text, "native_text"))
            pages.append(PageCarrier(source_position=position, page_position=0))
        elif mime == "application/pdf":
            with pdfium.PdfDocument(content) as document:
                if len(document) + len(pages) > limit:
                    raise ValidationError("Document exceeds its page limit.")
                for number in range(len(document)):
                    heartbeat()
                    with closing(document[number]) as page, closing(page.get_textpage()) as textpage:
                        text = textpage.get_text_range().strip()
                        if text:
                            parts.append(_text_part(position, number, text, "pdf_text"))
                            pages.append(PageCarrier(source_position=position, page_position=number))
                        else:
                            dimensions = page.get_size()
                            if min(dimensions) <= 0:
                                raise ValidationError("A PDF page requires positive dimensions.")
                            scale = min(dpi / 72, max_edge / max(dimensions))
                            with closing(page.render(scale=scale)) as bitmap:
                                pages.append(
                                    _raster(bitmap.to_pil(), row, position, number, dpi, max_edge, actor)
                                )
        elif mime.startswith("image/"):
            with Image.open(io.BytesIO(content)) as image:
                pages.append(_raster(image, row, position, 0, dpi, max_edge, actor))
        else:
            holds.append(f"unsupported_media_type:{position}")
        if len(pages) > limit or sum(len(json.dumps(p.value).encode()) for p in parts) > text_limit:
            raise ValidationError("Document exceeds its page or text limit.")
    carriers = []
    for part in parts:
        heartbeat()
        drive_id = str(files[part.source_position].drive.sqid) if part.source_position < len(files) else ""
        carriers.append(PartCarrier.retain(part, actor=actor, drive_id=drive_id))
    return PreparedDocument(sources=snapshots, pages=pages, parts=carriers, hold_reasons=holds)


def _raster(
    image: Image.Image, source: Any, position: int, number: int, dpi: int, maximum: int, actor: Any
) -> PageCarrier:
    image.thumbnail((maximum, maximum))
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=90)
    carrier = apps.get_model("storage.File").objects.ingest_bytes(
        output.getvalue(),
        filename=f"extraction-{position}-{number}.jpg",
        owner_id=actor_user_id(to_subject_ref(actor)),
        drive_id=str(source.drive.sqid),
    )
    return PageCarrier(
        source_position=position,
        page_position=number,
        image_file_id=str(carrier.sqid),
        image_digest=str(carrier.content_hash),
        width=image.width,
        height=image.height,
        dpi=dpi,
    )


def _text_part(
    source: int, page: int, text: str, method: str, *,
    kind: ExtractionPartKind = cast(ExtractionPartKind, ExtractionPartKind.NATIVE_TEXT),
    **metadata: Any,
) -> DocumentPart:
    return DocumentPart(
        source,
        page,
        "text/plain",
        kind,
        text,
        method,
        hashlib.sha256(text.encode()).hexdigest(),
        **metadata,
    )


class _Text(HTMLParser):
    """Extract inert HTML text using the standard parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored and data.strip():
            self.chunks.append(data.strip())
