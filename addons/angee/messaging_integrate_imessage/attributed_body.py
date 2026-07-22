"""Decode the plain message text out of an Apple ``NSAttributedString`` blob.

Newer iOS leaves ``message.text`` NULL and stores the body only in
``message.attributedBody`` — an ``NSAttributedString`` serialized with the legacy
``NSArchiver`` *typedstream* (``streamtyped``) format, not ``NSKeyedArchiver``. A
typedstream is a flat, self-describing byte stream: a header (``\\x04\\x0b`` +
``"streamtyped"``), then a sequence of class declarations and type-tagged values.

An ``NSAttributedString`` serializes as its backing ``NSString`` followed by the
attribute runs. The backing string is written as a typedstream *bytes* value —
type tag ``+`` (``0x2b``), a variable-length count, then the UTF-8 bytes — right
after the ``NSString``/``NSObject`` class chain. Apple's count is a signed
variable-length integer: a single byte when the value is ``< 0x80``, ``0x81``
followed by a little-endian ``uint16`` for larger counts (``0x82`` + ``uint32``
for very large ones). The attribute runs are further ``NSString``/``NSNumber``
values *after* the text, so the first bytes-value after the first ``NSString``
marker is the message text.

Reference: Apple's NeXT ``typedstream`` format (``NSArchiver``); the same layout
community tools such as ``imessage-exporter`` and ``imessagedb`` decode. This is a
deliberately minimal, stdlib-only reader of just the primary string — it never
raises, returning ``""`` on any structure it does not recognize.
"""

from __future__ import annotations

import struct

_STRING_MARKER = b"NSString"
"""The backing-string class name; the message text is the next bytes-value after it."""

_BYTES_TAG = 0x2B
"""Typedstream type tag (``+``) introducing an inline length-prefixed byte run."""

_UINT16_PREFIX = 0x81
"""Count prefix: the next two bytes are a little-endian ``uint16`` length."""

_UINT32_PREFIX = 0x82
"""Count prefix: the next four bytes are a little-endian ``uint32`` length."""


def attributed_body_text(blob: bytes | None) -> str:
    """Return the message text carried in an ``attributedBody`` blob, or ``""``.

    Defensive by contract: every unrecognized or truncated structure yields
    ``""`` rather than raising, so a store row with an exotic archive never
    breaks an import — the row simply lands without recovered text (its media,
    if any, still lands).
    """

    if not blob:
        return ""
    marker = blob.find(_STRING_MARKER)
    if marker < 0:
        return ""
    tag = blob.find(bytes([_BYTES_TAG]), marker + len(_STRING_MARKER))
    if tag < 0:
        return ""
    length, data_start = _read_count(blob, tag + 1)
    if length is None or data_start + length > len(blob):
        return ""
    return blob[data_start : data_start + length].decode("utf-8", "replace")


def _read_count(blob: bytes, offset: int) -> tuple[int | None, int]:
    """Read Apple's variable-length count at ``offset`` → ``(length, data_start)``.

    Returns ``(None, offset)`` when the prefix is malformed or truncated, so the
    caller degrades to empty text instead of guessing a length.
    """

    if offset >= len(blob):
        return None, offset
    lead = blob[offset]
    if lead < 0x80:
        return lead, offset + 1
    if lead == _UINT16_PREFIX:
        if offset + 3 > len(blob):
            return None, offset
        return struct.unpack_from("<H", blob, offset + 1)[0], offset + 3
    if lead == _UINT32_PREFIX:
        if offset + 5 > len(blob):
            return None, offset
        return struct.unpack_from("<I", blob, offset + 1)[0], offset + 5
    return None, offset
