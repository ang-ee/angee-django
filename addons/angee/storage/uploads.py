"""ORM-free byte helpers shared by the storage upload flow.

The upload behavior lives on the model (``File.objects.draft``,
``File.receive_bytes`` / ``File.finalize``); this module holds only the token
constants and byte-level helpers they compose with — stream hashing, MIME
detection, and the capped body reader. Keeping it free of any model import is
what lets ``models.py`` import from here without a cycle.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from typing import BinaryIO

import magic

logger = logging.getLogger(__name__)

FALLBACK_MIME = "application/octet-stream"
"""MIME used when libmagic yields nothing; also the guaranteed catalogue row."""

UPLOAD_TOKEN_MAX_AGE = 900
"""Seconds a proxy upload token stays valid."""

UPLOAD_TOKEN_SALT = "angee.storage.upload"
"""Signing salt namespacing proxy upload tokens."""

UPLOAD_TOKEN_HEADER = "X-Angee-Upload-Token"
"""Request header carrying the proxy upload token."""

DOWNLOAD_TOKEN_MAX_AGE = 900
"""Seconds a proxy download token stays valid."""

DOWNLOAD_TOKEN_SALT = "angee.storage.download"
"""Signing salt namespacing proxy download tokens."""

DOWNLOAD_TOKEN_HEADER = "X-Angee-Download-Token"
"""Request header carrying the proxy download token."""

PROXY_CHUNK_SIZE = 1 << 20
"""Bytes per read while streaming a proxied body into the backend."""

MIME_SNIFF_BYTES = 4096
"""Head bytes captured during finalize hashing for MIME detection."""


def sha256_stream(reader: BinaryIO, *, capture_head: int = 0) -> tuple[str, int, bytes]:
    """Stream-hash a binary reader without materializing it.

    Returns ``(hex_digest, total_bytes, head_bytes)``; ``head_bytes`` is the
    first ``capture_head`` bytes so callers can sniff MIME without a second
    read.
    """

    digest = hashlib.sha256()
    total = 0
    head = bytearray()
    while chunk := reader.read(PROXY_CHUNK_SIZE):
        digest.update(chunk)
        total += len(chunk)
        if capture_head and len(head) < capture_head:
            head.extend(chunk[: capture_head - len(head)])
    return digest.hexdigest(), total, bytes(head)


def detect_mime(payload: bytes, filename: str = "") -> str:
    """Detect a MIME type for a stored object.

    libmagic sniffs the head bytes and is authoritative when it recognises the
    content. It does not know every format (e.g. HEIC on older magic
    databases), so when it yields nothing we fall back to the filename
    extension via the stdlib ``mimetypes`` registry, so the row still carries a
    useful type instead of the generic catch-all.
    """

    try:
        detected = str(magic.from_buffer(payload, mime=True) or "")
    except (OSError, ValueError, magic.MagicException) as error:
        logger.warning("storage.finalize: libmagic detection failed: %s", error)
        detected = ""
    detected = detected.split(";", 1)[0].strip().lower()
    if detected and detected != FALLBACK_MIME:
        return detected
    guessed = mimetypes.guess_type(filename)[0] if filename else None
    return (guessed or FALLBACK_MIME).split(";", 1)[0].strip().lower()


class BodyTooLarge(Exception):
    """Internal sentinel raised by :class:`CappedReader` when the cap trips."""


class CappedReader:
    """File-like wrapper that aborts once more than ``max_bytes`` are read.

    Streams a request body into ``Storage.save`` without materializing it;
    Django's ``File`` wrapper only needs ``read(size)``. Overflow raises the
    private :class:`BodyTooLarge` sentinel so the caller can clean up the
    partial backend object before answering with an API error.
    """

    def __init__(self, reader: BinaryIO, *, max_bytes: int) -> None:
        """Wrap ``reader`` with a hard byte cap."""

        self._reader = reader
        self._max_bytes = max_bytes
        self._consumed = 0

    def read(self, size: int = -1) -> bytes:
        """Read up to ``size`` bytes, raising :class:`BodyTooLarge` on overflow."""

        if size in (None, -1):
            size = PROXY_CHUNK_SIZE
        remaining = self._max_bytes - self._consumed
        if remaining <= 0:
            if self._reader.read(1):
                raise BodyTooLarge
            return b""
        chunk = self._reader.read(min(size, remaining + 1))
        self._consumed += len(chunk)
        if self._consumed > self._max_bytes:
            raise BodyTooLarge
        return chunk

    def close(self) -> None:
        """Close the wrapped reader when it supports closing."""

        close = getattr(self._reader, "close", None)
        if callable(close):
            close()
