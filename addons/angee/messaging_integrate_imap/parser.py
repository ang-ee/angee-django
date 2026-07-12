"""Pure MIME parsing — raw RFC 822 bytes into the neutral messaging parse shapes.

The stdlib ``email`` package with ``policy.default`` owns the wire format (RFC 2047
header decoding, RFC 2231 filenames, charset handling, the multipart walk); this
module only maps its ``EmailMessage`` onto :class:`~angee.messaging.backends
.ParsedMessage`. The mapping rules that matter downstream:

- ``external_id`` is the RFC 5322 Message-ID; a message without one gets a stable
  ``sha256:`` digest of its raw bytes, so ID-less mail never collapses onto one row
  and never duplicates across re-syncs.
- ``text/plain`` bodies split into per-paragraph body/quoted/signature parts.
  The ``mail-parser-reply`` library owns the segmentation — reply boundaries at
  attribution headers ("On …, X wrote:", Outlook From:-blocks), signatures with
  or without the RFC 3676 ``--`` delimiter, trailing disclaimers — while quote
  markers are stripped locally, so a reply's quoted paragraphs content-address
  to the same ``Fragment`` rows as the original body and the quotation graph
  links them.
- an HTML-only message derives a plain body (so previews and search see text) and
  keeps the original HTML verbatim under a ``multipart/alternative`` container.
- attachments keep disposition and Content-ID, so inline images stay ``inline`` +
  ``cid`` and real attachments stay ``attachment``.
- a message the MIME parser cannot handle still lands: :func:`fallback_message`
  wraps the raw bytes as an attachment behind a best-effort envelope, so no mail
  is ever dropped and no retry ledger is needed.

Everything here is pure — no network, no database — so the whole matrix is unit
testable from literal byte strings. Null bytes pass through untouched; the
messaging managers own that scrub on the write path.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime
from email import message_from_bytes
from email import policy as email_policy
from email.header import decode_header, make_header
from email.message import EmailMessage, MIMEPart
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, cast

from mailparser_reply import EmailReplyParser

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedRecipient
from angee.parties.models import Handle

# The parties platform email handles live under (`Handle.Platform` owns the value).
_EMAIL_PLATFORM = str(Handle.Platform.EMAIL)

_MESSAGE_ID_RE = re.compile(r"<([^<>]+)>")
_QUOTE_MARKER_RE = re.compile(r"^\s*(?:>\s?)+")
_FILENAME_MAX_LENGTH = 512
# The RFC 3676 "-- " (or Outlook "__…") delimiter line that opens a signature
# match; dropped from the signature part so only the signature text lands.
_SIGNATURE_DELIMITER_RE = re.compile(r"[-_]{2,}\s*")
_UNSAFE_FILENAME_RE = re.compile(r"[/\\\x00]")

# mail-parser-reply owns plain-text segmentation (docs/stack.md): reply
# boundaries at attribution headers, signature and disclaimer detection. The
# language set covers what the previous hand-rolled attribution heuristic
# matched (en/fr/de/es/it) so non-English "On …, X wrote:" headers keep
# classifying as reply boundaries.
_REPLY_PARSER = EmailReplyParser(languages=["en", "fr", "de", "es", "it"])

# Envelope roles mapped from the address headers that carry them.
_RECIPIENT_HEADERS = (("To", "to"), ("Cc", "cc"), ("Bcc", "bcc"))

# Headers retained as standalone, queryable HEADER parts (fragment-backed, so a
# List-Id shared by a whole mailing list is one row). Everything else either has a
# better owner (addresses → Participant, Message-ID/References → identity/threading,
# Subject → the TITLE part, Date → sent_at, Content-* → the part tree) or is
# transport diagnostics that stay in the lossless ``metadata["headers"]`` envelope.
_RETAINED_HEADERS = (
    "list-id",
    "list-unsubscribe",
    "reply-to",
    "auto-submitted",
    "precedence",
)


def parse_message(
    raw: bytes,
    *,
    mailbox: str,
    uid: int,
    uidvalidity: int,
    flags: tuple[Any, ...] = (),
    internal_date: datetime | None = None,
    own_addresses: frozenset[str] = frozenset(),
    truncated_bytes: int | None = None,
) -> ParsedMessage:
    """Parse one raw RFC 822 message into a :class:`ParsedMessage`.

    ``internal_date`` is the server receipt time (the IMAP INTERNALDATE) and the
    fallback when the Date header is missing or malformed. ``own_addresses``
    classifies direction (an owned From is outbound; owned From *and* only owned
    recipients is internal). ``truncated_bytes`` marks a header-only fetch of an
    oversized message: the size lands in metadata and the body stays empty rather
    than the message being dropped.

    Raises whatever the MIME layer raises on hopeless input — the caller wraps
    with :func:`fallback_message` so a poison message still lands.
    """

    message = cast(EmailMessage, message_from_bytes(raw, policy=email_policy.default))
    sender = _first_handle(message.get_all("From", ()))
    recipients = _recipients(message)
    body = _parse_body(message)
    if truncated_bytes is None:
        body = _ensure_plain_body(body)
    metadata = _metadata(
        message,
        mailbox=mailbox,
        uid=uid,
        uidvalidity=uidvalidity,
        flags=flags,
        size=len(raw),
    )
    if truncated_bytes is not None:
        metadata["truncated_bytes"] = truncated_bytes
    return ParsedMessage(
        external_id=_message_id(message.get("Message-ID")) or synthetic_external_id(raw),
        platform=_EMAIL_PLATFORM,
        direction=_direction(sender, recipients, own_addresses),
        subject=str(message.get("Subject", "") or "").strip(),
        headers=_retained_headers(message),
        sender=sender,
        recipients=recipients,
        sent_at=_sent_at(message, internal_date),
        received_at=_aware(internal_date),
        in_reply_to=_message_id(message.get("In-Reply-To")),
        references=_references(message),
        body=body,
        metadata=metadata,
    )


def fallback_message(
    raw: bytes,
    *,
    mailbox: str,
    uid: int,
    uidvalidity: int,
    flags: tuple[Any, ...] = (),
    internal_date: datetime | None = None,
    error: Exception | None = None,
) -> ParsedMessage:
    """Wrap an unparseable raw message so it still lands instead of being lost.

    The lenient ``compat32`` policy recovers what headers it can for the envelope;
    the raw bytes ride as a ``message/rfc822`` attachment so nothing is discarded
    and the message can be re-parsed once the defect is understood.
    """

    subject = ""
    external_id = ""
    try:
        lenient = message_from_bytes(raw)  # compat32: never raises on header access
        subject = _decode_lenient_header(lenient.get("Subject"))
        external_id = _message_id(lenient.get("Message-ID"))
    except Exception:  # noqa: BLE001 — the fallback must survive any input.
        pass
    return ParsedMessage(
        external_id=external_id or synthetic_external_id(raw),
        platform=_EMAIL_PLATFORM,
        subject=subject,
        received_at=_aware(internal_date),
        sent_at=_aware(internal_date),
        body=ParsedPart(
            type="message/rfc822",
            disposition="attachment",
            name="message.eml",
            content=raw,
        ),
        metadata={
            "mailbox": mailbox,
            "uid": uid,
            "uidvalidity": uidvalidity,
            "flags": _flag_names(flags),
            "size": len(raw),
            "parse_error": f"{type(error).__name__}: {error}" if error is not None else "unparseable",
        },
    )


def synthetic_external_id(raw: bytes) -> str:
    """Return a stable dedup key for a message that carries no Message-ID.

    Hashing the raw bytes keys the same message identically in every folder and
    every re-sync, while two distinct ID-less messages never collapse onto one row.
    """

    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def split_plain_text(text: str) -> list[tuple[str, str]]:
    """Split plain text into ordered ``(role, paragraph)`` segments.

    ``mail-parser-reply`` owns the segmentation: it splits the text into replies
    at the attribution headers it recognizes and detects each reply's signature
    (the RFC 3676 ``-- `` delimiter or a salutation tail like "Best regards,")
    and trailing corporate disclaimers. Roles are the messaging part vocabulary:
    ``body`` paragraphs from the newest reply, ``quoted`` paragraphs for older
    replies and marker-quoted runs (attribution line included, markers stripped
    to any depth, so the text content-addresses to the original body's
    fragments), and ``signature`` for signatures and disclaimers alike.
    Paragraphs are blank-line separated; document order is preserved so the
    first body paragraph stays the preview.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return []
    segments: list[tuple[str, str]] = []
    for index, reply in enumerate(_REPLY_PARSER.read(normalized).replies):
        # The attribution header introduces the quote but stays its own segment —
        # glued to the first quoted paragraph it would break the
        # content-addressed match with the original body text.
        header = _strip_quote_markers(reply.headers or "")
        if header:
            segments.append(("quoted", header))
        segments.extend(_body_segments(reply.body, quoted=index > 0))
        signature = _signature_text(reply.signatures or "", reply.disclaimers)
        if signature:
            segments.append(("signature", signature))
        segments.extend(
            ("signature", _strip_quote_markers(disclaimer)) for disclaimer in reply.disclaimers if disclaimer.strip()
        )
    return segments


def html_to_text(html: str) -> str:
    """Extract readable text from an HTML body (script/style dropped).

    A deliberately small stdlib extractor for previews, search, and fragment
    dedup — rendering fidelity stays with the stored HTML part.
    """

    extractor = _TextExtractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:  # noqa: BLE001 — pathological markup degrades to what was collected.
        pass
    text = "".join(extractor.chunks)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class _TextExtractor(HTMLParser):
    """Collect text nodes, skipping non-content elements and marking breaks."""

    _SKIP_ELEMENTS = frozenset({"script", "style", "head", "title", "template"})
    _BREAK_ELEMENTS = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_ELEMENTS:
            self._skip_depth += 1
        elif tag in self._BREAK_ELEMENTS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_ELEMENTS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK_ELEMENTS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.chunks.append(data)


# --- envelope helpers ---


def _message_id(value: Any) -> str:
    """Return the inner value of a Message-ID-style header, or ``""``."""

    text = str(value or "").strip()
    if not text:
        return ""
    match = _MESSAGE_ID_RE.search(text)
    return (match.group(1) if match else text.strip("<>")).strip()


def _references(message: EmailMessage) -> tuple[str, ...]:
    """Return the References chain as bare message IDs, oldest first, deduplicated."""

    seen: dict[str, None] = {}
    for header in message.get_all("References", ()):
        text = str(header or "")
        tokens = _MESSAGE_ID_RE.findall(text) or text.split()
        for token in tokens:
            cleaned = token.strip().strip("<>")
            if cleaned:
                seen.setdefault(cleaned)
    return tuple(seen)


def _first_handle(headers: tuple[Any, ...] | list[Any]) -> ParsedHandle | None:
    """Return the first parsed address from ``headers``, or ``None``."""

    for name, address in getaddresses([str(value) for value in headers]):
        if address:
            return ParsedHandle(
                platform=_EMAIL_PLATFORM,
                value=address.strip().lower(),
                display_name=name.strip(),
            )
    return None


def _recipients(message: EmailMessage) -> tuple[ParsedRecipient, ...]:
    """Return the To/Cc/Bcc addresses with envelope roles, deduplicated per role."""

    recipients: list[ParsedRecipient] = []
    seen: set[tuple[str, str]] = set()
    for header, role in _RECIPIENT_HEADERS:
        values = [str(value) for value in message.get_all(header, ())]
        for name, address in getaddresses(values):
            cleaned = address.strip().lower()
            if not cleaned or (cleaned, role) in seen:
                continue
            seen.add((cleaned, role))
            recipients.append(
                ParsedRecipient(
                    handle=ParsedHandle(
                        platform=_EMAIL_PLATFORM,
                        value=cleaned,
                        display_name=name.strip(),
                    ),
                    role=role,
                )
            )
    return tuple(recipients)


def _direction(
    sender: ParsedHandle | None,
    recipients: tuple[ParsedRecipient, ...],
    own_addresses: frozenset[str],
) -> str:
    """Classify direction from the account's own addresses.

    The string vocabulary is the neutral seam contract
    (:class:`~angee.messaging.backends.ParsedMessage`); without a sender or any
    own-address knowledge everything is inbound.
    """

    if sender is None or sender.value not in own_addresses:
        return "inbound"
    if recipients and all(recipient.handle.value in own_addresses for recipient in recipients):
        return "internal"
    return "outbound"


def _sent_at(message: EmailMessage, internal_date: datetime | None) -> datetime | None:
    """Return the Date header as an aware datetime, falling back to INTERNALDATE."""

    value = message.get("Date")
    if value is not None:
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            return _aware(parsed)
    return _aware(internal_date)


def _aware(value: datetime | None) -> datetime | None:
    """Return ``value`` timezone-aware, assuming UTC when the source was naive."""

    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _retained_headers(message: EmailMessage) -> tuple[tuple[str, str], ...]:
    """Return the allow-listed headers as ``(name, value)`` pairs, document order.

    A multi-valued header (a forwarded chain's repeated ``Reply-To``) yields one
    pair per value; an undecodable value is skipped the same way the lossless
    envelope skips it.
    """

    pairs: list[tuple[str, str]] = []
    for name in _RETAINED_HEADERS:
        for value in message.get_all(name, ()):
            try:
                text = str(value).strip()
            except Exception:  # noqa: BLE001 — one undecodable header must not drop the message.
                continue
            if text:
                pairs.append((name, text))
    return tuple(pairs)


def _metadata(
    message: EmailMessage,
    *,
    mailbox: str,
    uid: int,
    uidvalidity: int,
    flags: tuple[Any, ...],
    size: int,
) -> dict[str, Any]:
    """Return the lossless envelope metadata stored beside the message row."""

    headers: dict[str, list[str]] = {}
    for name in set(message.keys()):
        values: list[str] = []
        for value in message.get_all(name, ()):
            try:
                values.append(str(value))
            except Exception:  # noqa: BLE001 — one undecodable header must not drop the envelope.
                continue
        if values:
            headers[name] = values
    return {
        "mailbox": mailbox,
        "uid": uid,
        "uidvalidity": uidvalidity,
        "flags": _flag_names(flags),
        "size": size,
        "headers": headers,
    }


def _flag_names(flags: tuple[Any, ...]) -> list[str]:
    """Return IMAP flags as sorted text (``\\Seen`` bytes and str both accepted)."""

    return sorted(flag.decode("ascii", "replace") if isinstance(flag, bytes) else str(flag) for flag in flags)


def _decode_lenient_header(value: Any) -> str:
    """Best-effort decode of a compat32 header for the fallback envelope."""

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(make_header(decode_header(text)))
    except Exception:  # noqa: BLE001 — keep the raw text when RFC 2047 decoding fails.
        return text


# --- body tree ---


def _parse_body(message: MIMEPart) -> ParsedPart | None:
    """Map the MIME structure onto the recursive neutral part tree."""

    if message.get_content_maintype() == "message":
        return _embedded_message_part(message)
    if message.is_multipart():
        children = tuple(
            part for part in (_parse_body(sub) for sub in message.iter_parts()) if part is not None
        )
        if not children:
            return None
        return ParsedPart(type=message.get_content_type(), children=children)
    return _parse_leaf(message)


def _parse_leaf(message: MIMEPart) -> ParsedPart | None:
    """Map one non-multipart MIME leaf onto a text or byte part."""

    content_type = message.get_content_type()
    disposition = message.get_content_disposition()
    cid = _message_id(message.get("Content-ID"))
    if message.get_content_maintype() == "text" and disposition != "attachment":
        text = _text_content(message)
        if content_type == "text/plain":
            return _plain_part(text)
        return ParsedPart(type=content_type, text=text, cid=cid) if text else None
    content = _bytes_content(message)
    return ParsedPart(
        type=content_type,
        disposition="inline" if disposition == "inline" or (cid and disposition is None) else "attachment",
        name=_safe_filename(message.get_filename() or ""),
        cid=cid,
        content=content,
    )


def _plain_part(text: str) -> ParsedPart | None:
    """Shape plain text into a single body part or a role-segmented container."""

    segments = split_plain_text(text)
    if not segments:
        return None
    if len(segments) == 1 and segments[0][0] == "body":
        return ParsedPart(type="text/plain", text=segments[0][1])
    return ParsedPart(
        type="text/plain",
        children=tuple(ParsedPart(type="text/plain", role=role, text=part_text) for role, part_text in segments),
    )


def _embedded_message_part(message: MIMEPart) -> ParsedPart:
    """Keep an attached ``message/rfc822`` as raw bytes so nothing is lost."""

    try:
        embedded = message.get_content()
        raw = bytes(embedded)
        subject = str(embedded.get("Subject", "") or "").strip()
    except Exception:  # noqa: BLE001 — an undecodable embedded message still lands as bytes.
        raw = _decoded_payload(message) or b""
        subject = ""
    name = _safe_filename(f"{subject}.eml" if subject else "message.eml")
    return ParsedPart(type="message/rfc822", disposition="attachment", name=name, content=raw)


def _text_content(message: MIMEPart) -> str:
    """Decode a text leaf, degrading through declared charset → UTF-8 → Latin-1."""

    try:
        return str(message.get_content())
    except Exception:  # noqa: BLE001 — unknown/lying charsets fall through to the manual chain.
        payload = _decoded_payload(message) or b""
        for charset in (message.get_content_charset() or "utf-8", "utf-8"):
            try:
                return payload.decode(charset)
            except (LookupError, UnicodeDecodeError):
                continue
        return payload.decode("latin-1", errors="replace")


def _bytes_content(message: MIMEPart) -> bytes:
    """Decode a byte leaf's transfer encoding, degrading to the raw payload."""

    payload = _decoded_payload(message)
    if payload is not None:
        return payload
    fallback = message.get_payload()
    if isinstance(fallback, str):
        return fallback.encode("utf-8", errors="replace")
    return b""


def _decoded_payload(message: MIMEPart) -> bytes | None:
    """Return the leaf's transfer-decoded bytes, or ``None`` when undecodable.

    ``get_payload(decode=True)`` is typed (and behaves) as bytes-or-not: a
    multipart or broken part yields no bytes; the isinstance guard owns that
    narrowing once for every caller.
    """

    try:
        payload = message.get_payload(decode=True)
    except Exception:  # noqa: BLE001 — a broken transfer encoding is "no decodable bytes".
        return None
    return payload if isinstance(payload, bytes) else None


def _safe_filename(name: str) -> str:
    """Return a storage-safe display filename for one MIME part."""

    safe = _UNSAFE_FILENAME_RE.sub("_", name).strip()
    if len(safe) <= _FILENAME_MAX_LENGTH:
        return safe
    stem, dot, suffix = safe.rpartition(".")
    extension = f"{dot}{suffix}" if dot and stem else ""
    if extension and len(extension) < _FILENAME_MAX_LENGTH:
        return f"{stem[: _FILENAME_MAX_LENGTH - len(extension)]}{extension}"
    return safe[:_FILENAME_MAX_LENGTH]


def _ensure_plain_body(body: ParsedPart | None) -> ParsedPart | None:
    """Give an HTML-only message a derived plain body beside the original HTML.

    Previews, search, and the quotation graph all read plain-text fragments; the
    first HTML body node gains a ``multipart/alternative`` wrapper whose first
    child is the derived (and role-split) plain text, mirroring how a
    well-formed sender would have shaped the message.
    """

    if body is None or _has_plain_body(body):
        return body
    rewritten, _replaced = _wrap_first_html(body)
    return rewritten


def _has_plain_body(part: ParsedPart) -> bool:
    """Return whether any non-quoted plain-text node exists in the tree."""

    if part.type == "text/plain" and part.text and part.role == "body":
        return True
    return any(_has_plain_body(child) for child in part.children)


def _wrap_first_html(part: ParsedPart) -> tuple[ParsedPart, bool]:
    """Wrap the first HTML body node with a derived-plain alternative."""

    if part.type == "text/html" and part.text and part.role == "body" and part.content is None:
        plain = _plain_part(html_to_text(part.text))
        if plain is None:
            return part, True
        return ParsedPart(type="multipart/alternative", children=(plain, part)), True
    children: list[ParsedPart] = []
    replaced = False
    for child in part.children:
        if replaced:
            children.append(child)
            continue
        rewritten, replaced = _wrap_first_html(child)
        children.append(rewritten)
    if replaced:
        return replace(part, children=tuple(children)), True
    return part, False


# --- plain-text segmentation helpers ---


def _body_segments(body: str, *, quoted: bool) -> list[tuple[str, str]]:
    """Classify one reply body's paragraphs as ``body`` or ``quoted``.

    The reply parser leaves unattributed ``>`` blocks inline, so runs of
    marker-quoted lines become ``quoted`` with the markers stripped — that keeps
    the paragraph text byte-identical to the original message's body. A
    non-first reply is quoted wholesale: an Outlook-style bottom quote carries
    no markers at all.
    """

    segments: list[tuple[str, str]] = []
    run_role = ""
    run_lines: list[str] = []

    def flush() -> None:
        if not run_lines:
            return
        content = "\n".join(run_lines)
        if run_role == "quoted":
            content = _strip_quote_markers(content)
        segments.extend((run_role, paragraph) for paragraph in _paragraphs(content))

    for line in body.split("\n"):
        role = "quoted" if quoted or _QUOTE_MARKER_RE.match(line) else "body"
        if role != run_role:
            flush()
            run_role = role
            run_lines = []
        run_lines.append(line)
    flush()
    return segments


def _strip_quote_markers(text: str) -> str:
    """Strip leading ``>`` markers, to any depth, from every line of ``text``."""

    return "\n".join(_QUOTE_MARKER_RE.sub("", line) for line in text.split("\n")).strip()


def _signature_text(signature: str, disclaimers: list[str]) -> str:
    """Return the signature block without its delimiter line or embedded disclaimers.

    The library's signature match runs from the delimiter line to the end of the
    reply, so a disclaimer below the signature rides inside it; it is removed
    here (mirroring ``EmailReply.body``) because it lands as its own segment.
    """

    for disclaimer in disclaimers:
        signature = signature.replace(disclaimer, "")
    lines = _strip_quote_markers(signature).split("\n")
    if lines and _SIGNATURE_DELIMITER_RE.fullmatch(lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _paragraphs(text: str) -> list[str]:
    """Return blank-line-separated, stripped paragraphs of ``text``."""

    return [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
