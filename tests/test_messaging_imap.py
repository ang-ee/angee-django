"""Tests for the IMAP channel backend — parse, incremental cursor, and end to end.

Three layers, mirroring the addon's split: the pure MIME parser is exercised from
literal byte strings (no network, no database); the backend's discovery/cursor/
batching logic runs against an in-memory fake of the IMAPClient surface; and the
full path — ``Channel.run_sync`` draining the backend through the messaging
ingest onto real tables — pins threading, parts, attachments, idempotent
re-sync, and the crash-safe cursor contract.
"""

from __future__ import annotations

import ssl
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import ValidationError
from hypothesis import example, given, settings
from hypothesis import strategies as st
from imapclient.exceptions import IMAPClientError, LoginError
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.integrate.states import StreamDefinition
from angee.integrate.streams import BridgeSyncError, CursorInvalid, advance_stream, open_stream
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.managers import AUTOMATED_MAIL_HEADERS
from angee.messaging.testing.models import Handle, Message, MessageEdge, Part, Participant, Thread
from angee.messaging_integrate_imap import backend as imap_backend
from angee.messaging_integrate_imap import parser as imap_parser
from angee.messaging_integrate_imap.backend import _SEARCH_UID_WINDOW as WINDOW
from angee.messaging_integrate_imap.backend import (
    MAX_SAMPLE_MESSAGES,
    ImapChannelBackend,
    ImapError,
    ImapSamplePreviewRequest,
)
from angee.messaging_integrate_imap.models import ImapChannelSampling
from angee.messaging_integrate_imap.parser import (
    fallback_message,
    html_to_text,
    parse_message,
    split_plain_text,
    synthetic_external_id,
)
from angee.messaging_integrate_imap.testing.server import INTERNAL_DATE, ImapServer, mailbox, serve
from angee.messaging_integrate_imap.testing.server import imap_server as imap_server
from angee.storage.exceptions import UploadTargetNotFound
from tests.conftest import current_stream, make_integration
from tests.stream_adapters import AdapterPages
from tests.test_messaging import _storage_drive
from tests.test_messaging_graphql import Channel


def _eml(
    *,
    message_id: str = "<m1@example.com>",
    subject: str = "Hello",
    sender: str = "Ada Lovelace <ada@example.com>",
    to: str = "Bob <bob@example.com>",
    extra_headers: str = "",
    body: str = "Hi Bob,\n\nSee you Thursday.\n",
) -> bytes:
    """Build a simple text/plain RFC 822 message."""

    headers = [
        f"From: {sender}",
        f"To: {to}",
        f"Subject: {subject}",
        "Date: Thu, 02 Jul 2026 10:00:00 +0000",
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="utf-8"',
    ]
    if message_id:
        headers.append(f"Message-ID: {message_id}")
    if extra_headers:
        headers.append(extra_headers.strip())
    return ("\r\n".join(headers) + "\r\n\r\n" + body).encode("utf-8")


def _parse(raw: bytes, **overrides: Any) -> Any:
    """Parse ``raw`` with the boring envelope defaults used across cases."""

    values: dict[str, Any] = {
        "mailbox": "INBOX",
        "uid": 7,
        "uidvalidity": 100,
        "flags": (b"\\Seen",),
        "internal_date": INTERNAL_DATE,
    }
    values.update(overrides)
    return parse_message(raw, **values)


# --- parser: envelope ---


def test_parse_full_email_maps_envelope() -> None:
    """Headers map to the neutral envelope: id, addresses, roles, times, threading."""

    raw = _eml(
        subject="=?utf-8?q?R=C3=A9union?=",
        to="Bob <bob@example.com>, carol@example.com",
        extra_headers=(
            "Cc: Dan <dan@example.com>\r\n"
            "In-Reply-To: <root@example.com>\r\n"
            "References: <root@example.com> <mid@example.com>"
        ),
    )
    parsed = _parse(raw)
    assert parsed.external_id == "m1@example.com"
    assert parsed.platform == "email"
    assert parsed.subject == "Réunion"
    assert parsed.sender.value == "ada@example.com"
    assert parsed.sender.display_name == "Ada Lovelace"
    assert [(r.handle.value, r.role) for r in parsed.recipients] == [
        ("bob@example.com", "to"),
        ("carol@example.com", "to"),
        ("dan@example.com", "cc"),
    ]
    assert parsed.in_reply_to == "root@example.com"
    assert parsed.references == ("root@example.com", "mid@example.com")
    assert parsed.sent_at == datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    assert parsed.received_at == INTERNAL_DATE
    assert parsed.metadata["mailbox"] == "INBOX"
    assert parsed.metadata["uid"] == 7
    assert parsed.metadata["uidvalidity"] == 100
    assert parsed.metadata["flags"] == ["\\Seen"]
    assert parsed.metadata["headers"]["Subject"] == ["Réunion"]


def test_missing_message_id_gets_stable_synthetic_id() -> None:
    """ID-less mail keys on a raw-bytes digest: stable per message, distinct across."""

    first = _eml(message_id="", subject="One")
    second = _eml(message_id="", subject="Two")
    assert _parse(first).external_id == _parse(first).external_id
    assert _parse(first).external_id.startswith("sha256:")
    assert _parse(first).external_id != _parse(second).external_id
    assert _parse(first).external_id == synthetic_external_id(first)


def test_overlong_message_id_is_preserved() -> None:
    """A valid but long RFC Message-ID remains the message idempotency key."""

    long_message_id = f"outlook-{'x' * 700}@example.com"
    raw = _eml(
        message_id=f"<{long_message_id}>",
        extra_headers=(f"In-Reply-To: <{long_message_id}>\r\nReferences: <root@example.com> <{long_message_id}>"),
    )

    parsed = _parse(raw)

    assert parsed.external_id == long_message_id
    assert parsed.in_reply_to == long_message_id
    assert parsed.references == ("root@example.com", long_message_id)
    assert len(parsed.external_id) > 512
    assert parsed.metadata["headers"]["Message-ID"] == [f"<{long_message_id}>"]


def test_malformed_date_falls_back_to_internal_date() -> None:
    """A garbage Date header falls back to the server receipt time."""

    raw = _eml().replace(b"Date: Thu, 02 Jul 2026 10:00:00 +0000", b"Date: not a date")
    assert _parse(raw).sent_at == INTERNAL_DATE


def test_direction_classifies_from_own_addresses() -> None:
    """Own From is outbound; own From with only own recipients is internal."""

    own = frozenset({"ada@example.com"})
    outbound = _eml(sender="ada@example.com", to="bob@example.com")
    internal = _eml(sender="ada@example.com", to="Ada <ADA@example.com>")
    inbound = _eml(sender="bob@example.com", to="ada@example.com")
    assert _parse(outbound, own_addresses=own).direction == "outbound"
    assert _parse(internal, own_addresses=own).direction == "internal"
    assert _parse(inbound, own_addresses=own).direction == "inbound"
    assert _parse(outbound).direction == "inbound"  # no own-address knowledge


# --- parser: plain-text segmentation ---


def test_split_plain_text_separates_body_quote_and_signature() -> None:
    """Paragraphs keep document order; quotes strip markers; signature splits off."""

    text = (
        "Thanks, sounds good.\n"
        "\n"
        "Second thought below.\n"
        "\n"
        "On Thu, Jul 2, 2026 Bob wrote:\n"
        "> Are we still on for Thursday?\n"
        ">\n"
        ">> Original nested line.\n"
        "\n"
        "-- \n"
        "Ada Lovelace\n"
        "Analytical Engines\n"
    )
    segments = split_plain_text(text)
    assert segments == [
        ("body", "Thanks, sounds good."),
        ("body", "Second thought below."),
        ("quoted", "On Thu, Jul 2, 2026 Bob wrote:"),
        ("quoted", "Are we still on for Thursday?"),
        ("quoted", "Original nested line."),
        ("signature", "Ada Lovelace\nAnalytical Engines"),
    ]


def test_split_plain_text_single_paragraph_stays_single_body_segment() -> None:
    """A one-paragraph message yields exactly one body segment."""

    assert split_plain_text("Just one line.") == [("body", "Just one line.")]
    assert split_plain_text("") == []


def test_quoted_paragraph_content_addresses_to_the_original_body() -> None:
    """A reply's quoted paragraph equals the original body paragraph verbatim.

    This is what makes the shared-fragment quotation graph link the two messages:
    both texts hash to the same content-addressed Fragment row.
    """

    original = "Are we still on for Thursday?"
    reply_text = f"Yes!\n\n> {original}\n"
    quoted = [text for role, text in split_plain_text(reply_text) if role == "quoted"]
    assert quoted == [original]


def test_body_parts_carry_roles_in_the_parsed_tree() -> None:
    """A split plain body becomes a container of role-tagged text parts."""

    raw = _eml(body="Reply here.\n\n> Quoted line.\n\n-- \nSig\n")
    body = _parse(raw).body
    assert body.type == "text/plain"
    assert [(child.role, child.text) for child in body.children] == [
        ("body", "Reply here."),
        ("quoted", "Quoted line."),
        ("signature", "Sig"),
    ]


def test_signature_without_delimiter_lands_as_signature_part() -> None:
    """A salutation-tail signature with no RFC 3676 ``--`` line still gets the role."""

    raw = _eml(body="Deal confirmed for Thursday.\n\nBest regards,\nAda Lovelace\n")
    body = _parse(raw).body
    assert [(child.role, child.text) for child in body.children] == [
        ("body", "Deal confirmed for Thursday."),
        ("signature", "Best regards,\nAda Lovelace"),
    ]


def test_corporate_disclaimer_maps_to_signature_role() -> None:
    """A trailing corporate disclaimer paragraph lands with the signature role."""

    text = "Numbers attached.\n\nDisclaimer: This email and any attachments are confidential.\n"
    assert split_plain_text(text) == [
        ("body", "Numbers attached."),
        ("signature", "Disclaimer: This email and any attachments are confidential."),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "Take the side entrance; the host is on +1 415 555 2671.",
        "Khalid will call you from +44 20 7946 0958.",
        "/etc/app/config.yaml holds the settings.",
        "Data: 42 rows\nDo: call the vendor",
    ],
    ids=["take", "kh", "path", "header-like"],
)
def test_ordinary_english_lines_stay_body(line: str) -> None:
    """No language the parser applies may read an English body line as a signature or a reply boundary."""

    assert split_plain_text(f"Thanks for the note.\n\n{line}\n\nBest,\nAlexis") == [
        ("body", "Thanks for the note."),
        ("body", line),
        ("signature", "Best,\nAlexis"),
    ]


@pytest.mark.parametrize(
    ("quote", "header"),
    [
        pytest.param(
            "On Thu, Jul 2, 2026 Monica wrote:\n> Here are the papers.\n>\n"
            "> Best,\n> Monica Alvarez\n> +1 787 523 6508\n",
            "On Thu, Jul 2, 2026 Monica wrote:",
            id="marker-quoted",
        ),
        pytest.param(
            "From: Monica Alvarez <monica@example.com>\nSent: Thursday, July 2, 2026\nSubject: Papers\n\n"
            "Here are the papers.\n\nBest,\nMonica Alvarez\n+1 787 523 6508\n",
            "From: Monica Alvarez <monica@example.com>\nSent: Thursday, July 2, 2026\nSubject: Papers",
            id="outlook",
        ),
        pytest.param(
            "Od: Monica Alvarez <monica@example.com>\nDatum: 2. července 2026 10:57:26 SELČ\nPředmět: Papers\n\n"
            "Here are the papers.\n\nBest,\nMonica Alvarez\n+1 787 523 6508\n",
            "Od: Monica Alvarez <monica@example.com>\nDatum: 2. července 2026 10:57:26 SELČ\nPředmět: Papers",
            id="czech-forward",
        ),
    ],
)
def test_quoted_reply_signature_stays_with_the_quoted_author(quote: str, header: str) -> None:
    """Only the sender's signature has the signature role; a quoted author's is quoted."""

    assert split_plain_text(f"Sounds good.\n\nBest,\nAlexis\n\n{quote}") == [
        ("body", "Sounds good."),
        ("signature", "Best,\nAlexis"),
        ("quoted", header),
        ("quoted", "Here are the papers."),
        ("quoted", "Best,\nMonica Alvarez\n+1 787 523 6508"),
    ]


# --- parser: MIME structure ---


def test_multipart_with_attachment_and_inline_image() -> None:
    """Attachments keep bytes/name/disposition; a CID part stays inline."""

    raw = (
        b"From: ada@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Files\r\n"
        b"Message-ID: <files@example.com>\r\n"
        b"Date: Thu, 02 Jul 2026 10:00:00 +0000\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="B1"\r\n'
        b"\r\n"
        b"--B1\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"See attached.\r\n"
        b"--B1\r\n"
        b"Content-Type: text/plain; name=notes.txt\r\n"
        b"Content-Disposition: attachment; filename=notes.txt\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"UExBSU5EQVRB\r\n"
        b"--B1\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-ID: <logo@cid>\r\n"
        b"Content-Disposition: inline\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"iVBORw0=\r\n"
        b"--B1--\r\n"
    )
    body = _parse(raw).body
    assert body.type == "multipart/mixed"
    text, attachment, inline = body.children
    assert (text.type, text.text) == ("text/plain", "See attached.")
    assert (attachment.disposition, attachment.name, attachment.content) == (
        "attachment",
        "notes.txt",
        b"PLAINDATA",
    )
    assert (inline.disposition, inline.cid, inline.type) == ("inline", "logo@cid", "image/png")


@pytest.mark.parametrize("disposition,expected", [("ATTACHMENT", "attachment"), ("inline", "inline"), (None, "inline")])
def test_content_id_preserves_explicit_attachment_disposition(disposition: str | None, expected: str) -> None:
    """Content-ID identifies a PDF; its explicit disposition still owns presentation."""
    disposition_header = f"Content-Disposition: {disposition}; filename=invoice.pdf\r\n" if disposition else ""
    raw = (
        "From: ada@example.com\r\n"
        "To: bob@example.com\r\n"
        "Subject: Document\r\n"
        "Message-ID: <cid-document@example.com>\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: application/pdf; name=invoice.pdf\r\n"
        "Content-ID: <document@example.com>\r\n"
        f"{disposition_header}\r\n"
        "%PDF-1.4 synthetic document"
    ).encode()
    part = _parse(raw).body
    assert (part.type, part.disposition, part.cid, part.name) == (
        "application/pdf", expected, "document@example.com", "invoice.pdf",
    )
    assert part.content == b"%PDF-1.4 synthetic document"


def test_attachment_filename_is_preserved_for_its_write_owner() -> None:
    """Parsing retains the decoded name; the shared part/File writes bound it."""

    long_name = f"{'x' * 588}.pdf"
    raw = (
        "From: ada@example.com\r\n"
        "To: bob@example.com\r\n"
        "Subject: Long attachment name\r\n"
        "Message-ID: <long-attachment@example.com>\r\n"
        "Date: Thu, 02 Jul 2026 10:00:00 +0000\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="B1"\r\n'
        "\r\n"
        "--B1\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "See attached.\r\n"
        "--B1\r\n"
        f'Content-Type: application/pdf; name="{long_name}"\r\n'
        f'Content-Disposition: attachment; filename="{long_name}"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "UERGREFUQQ==\r\n"
        "--B1--\r\n"
    ).encode()
    body = _parse(raw).body

    attachment = body.children[1]

    assert len(long_name) == 592
    assert attachment.name == long_name
    assert attachment.content == b"PDFDATA"


def test_html_only_message_derives_a_plain_body() -> None:
    """HTML-only mail gains a derived plain body; the HTML stays verbatim beside it."""

    html = "<html><head><style>p{}</style></head><body><p>Hello <b>Bob</b></p><script>x()</script></body></html>"
    raw = (
        b"From: ada@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Rich\r\n"
        b"Message-ID: <rich@example.com>\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n" + html.encode()
    )
    body = _parse(raw).body
    assert body.type == "multipart/alternative"
    plain, original = body.children
    assert plain.type == "text/plain"
    assert plain.text == "Hello Bob"
    assert original.type == "text/html"
    assert original.text == html


def test_alternative_with_plain_body_keeps_html_unwrapped() -> None:
    """A well-formed alternative keeps both parts without a second derivation."""

    raw = (
        b"From: ada@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Alt\r\n"
        b"Message-ID: <alt@example.com>\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/alternative; boundary="B2"\r\n'
        b"\r\n"
        b"--B2\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body.\r\n"
        b"--B2\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>Plain body.</p>\r\n"
        b"--B2--\r\n"
    )
    body = _parse(raw).body
    assert body.type == "multipart/alternative"
    assert [child.type for child in body.children] == ["text/plain", "text/html"]


def test_embedded_rfc822_message_lands_as_attachment_bytes() -> None:
    """A forwarded message keeps raw provenance and bounded nested evidence."""

    inner = (
        b"From: sender@example.com\r\n"
        b"To: reviewer@example.com\r\n"
        b"Subject: Inner report\r\n"
        b"Message-ID: <inner@example.com>\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="INNER"\r\n\r\n'
        b"--INNER\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<p>Document DOC-42 is attached.</p>\r\n"
        b"--INNER\r\nContent-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=document.pdf\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0xLjQK\r\n"
        b"--INNER--\r\n"
    )
    raw = (
        b"From: ada@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Fwd\r\n"
        b"Message-ID: <fwd@example.com>\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="B3"\r\n'
        b"\r\n"
        b"--B3\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"FYI.\r\n"
        b"--B3\r\n"
        b"Content-Type: message/rfc822\r\n"
        b"\r\n" + inner + b"\r\n"
        b"--B3--\r\n"
    )
    body = _parse(raw).body
    embedded = body.children[1]
    assert embedded.type == "message/rfc822"
    assert embedded.disposition == "attachment"
    assert embedded.name == "Inner report.eml"
    assert b"Message-ID: <inner@example.com>" in embedded.content
    assert embedded.children
    nested = embedded.children[0]
    assert nested.type == "multipart/mixed"
    assert nested.children[0].type == "multipart/alternative"
    assert [part.type for part in nested.children[0].children] == ["text/plain", "text/html"]
    assert nested.children[1].type == "application/pdf"


def test_embedded_rfc822_expansion_limit_retains_raw_parent() -> None:
    """A bounded-out attached message remains raw evidence for explicit review."""

    parts = b"".join(b"--P\r\nContent-Type: text/plain\r\n\r\npart\r\n" for _ in range(300))
    inner = (
        b"From: ada@example.com\r\nSubject: Bounded report\r\nMessage-ID: <inner-limit@example.com>\r\n"
        b'MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="P"\r\n\r\n' + parts + b"--P--\r\n"
    )
    raw = (
        b"From: ada@example.com\r\nTo: bob@example.com\r\nSubject: Fwd\r\n"
        b"Message-ID: <fwd-limit@example.com>\r\nMIME-Version: 1.0\r\n"
        b"Content-Type: message/rfc822\r\n\r\n" + inner
    )
    embedded = _parse(raw).body  # 301 parts exceed the expansion budget
    assert embedded.type == "message/rfc822"
    assert embedded.content
    assert embedded.children == ()


def test_delivery_report_keeps_status_blocks_and_expands_attached_message() -> None:
    """A DSN report is not mislabeled as an empty attached email."""

    attached = (
        b"From: sender@example.com\r\nSubject: Document\r\nMessage-ID: <document@example.com>\r\n"
        b"MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<p>Document DOC-42 is attached.</p>\r\n"
    )
    raw = (
        b"From: postmaster@example.com\r\nTo: reviewer@example.com\r\nSubject: Delivery report\r\n"
        b"Message-ID: <dsn@example.com>\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/report; boundary="REPORT"; report-type=delivery-status\r\n\r\n'
        b"--REPORT\r\nContent-Type: text/plain\r\n\r\nDelivery was delayed.\r\n"
        b"--REPORT\r\nContent-Type: message/delivery-status\r\n\r\n"
        b"Reporting-MTA: dns; example.com\r\n\r\nFinal-Recipient: rfc822; reviewer@example.com\r\n"
        b"Action: delayed\r\nStatus: 4.0.0\r\n\r\n"
        b"--REPORT\r\nContent-Type: message/rfc822\r\n\r\n" + attached + b"\r\n--REPORT--\r\n"
    )
    body = _parse(raw).body
    assert body.type == "multipart/report"
    status, forwarded = body.children[1:]
    assert status.type == "message/delivery-status"
    assert status.content
    assert len(status.children) == 2
    assert all(child.role == "header" for child in status.children)
    assert forwarded.type == "message/rfc822"
    assert forwarded.content
    assert forwarded.children[0].type == "multipart/alternative"
    assert [child.type for child in forwarded.children[0].children] == ["text/plain", "text/html"]


def test_outer_html_and_embedded_plain_are_normalized_per_message() -> None:
    """Nested plain text does not suppress the outer envelope's HTML fallback."""

    inner = _eml(message_id="<inner-plain@example.com>", subject="Inner plain")
    raw = (
        b"From: ada@example.com\r\nTo: bob@example.com\r\nSubject: Outer HTML\r\n"
        b"Message-ID: <outer-html@example.com>\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="OUTERHTML"\r\n\r\n'
        b"--OUTERHTML\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Outer context.</p>\r\n"
        b"--OUTERHTML\r\nContent-Type: message/rfc822\r\n\r\n" + inner + b"\r\n--OUTERHTML--\r\n"
    )
    body = _parse(raw).body
    assert body.children[0].type == "multipart/alternative"
    assert [part.type for part in body.children[0].children] == ["text/plain", "text/html"]
    assert body.children[1].type == "message/rfc822"
    assert body.children[1].children


def test_unknown_charset_degrades_without_dropping_the_body() -> None:
    """A lying/unknown charset decodes through the fallback chain."""

    raw = (
        b"From: ada@example.com\r\n"
        b"Subject: Odd\r\n"
        b"Message-ID: <odd@example.com>\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: text/plain; charset="x-no-such-charset"\r\n'
        b"\r\n"
        b"caf\xe9 body\r\n"
    )
    body = _parse(raw).body
    assert body.text == "café body"


def test_truncated_fetch_keeps_the_envelope_and_marks_the_size() -> None:
    """An oversized message's header-only parse lands with a truncation marker."""

    headers_only = _eml().split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
    parsed = _parse(headers_only, truncated_bytes=99_000_000)
    assert parsed.external_id == "m1@example.com"
    assert parsed.metadata["truncated_bytes"] == 99_000_000
    assert parsed.body is None


def test_fallback_message_wraps_raw_bytes_and_records_the_error() -> None:
    """A poison message still lands: best-effort envelope + raw .eml attachment."""

    raw = _eml(subject="Recovered")
    parsed = fallback_message(
        raw,
        mailbox="INBOX",
        uid=9,
        uidvalidity=100,
        internal_date=INTERNAL_DATE,
        error=ValueError("boom"),
    )
    assert parsed.external_id == "m1@example.com"
    assert parsed.subject == "Recovered"
    assert parsed.body.type == "message/rfc822"
    assert parsed.body.content == raw
    assert parsed.metadata["parse_error"] == "ValueError: boom"


def test_html_to_text_extracts_readable_text() -> None:
    """Tags become breaks, entities decode, script/style/head are dropped."""

    html = "<html><head><title>t</title></head><body><p>A &amp; B</p><div>C</div><script>no()</script></body></html>"
    assert html_to_text(html) == "A & B\n\nC"


# --- backend: discovery, cursor, batching (fake client, no database) ---


class _BridgeStub:
    """Just enough of a Channel row for the backend's transport/cursor logic."""

    def __init__(self, *, config: dict[str, Any] | None = None, credential: Any = None) -> None:
        self.config = {"host": "192.0.2.10", **(config or {})}
        self.credential = credential
        self._state = SimpleNamespace(adding=False, db="default")

    imap_delivery_boundary = ImapChannelSampling.imap_delivery_boundary

    def fresh_credential(self) -> Any:
        """Supply the integration credential contract without a database."""

        return self.credential


class _BasicCredentialStub:
    """A revealed basic-auth credential without the database."""

    kind = CredentialKind.BASIC_AUTH
    external_account = None

    def reveal(self) -> dict[str, str]:
        return {"username": "ada@example.com", "password": "pw"}


class _OAuthCredentialStub:
    """A refreshable OAuth credential without the database."""

    kind = CredentialKind.OAUTH
    external_account = None

    def __init__(self) -> None:
        self.freshened = 0

    def ensure_fresh(self) -> None:
        self.freshened += 1

    def secret_value(self) -> str:
        return "token-123"

    def reveal(self) -> dict[str, str]:
        return {"access_token": "token-123"}


def _backend(*, config: dict[str, Any] | None = None, credential: Any = None) -> ImapChannelBackend:
    """Bind an ImapChannelBackend to a stub bridge; it dials the ``imap_server`` fixture."""

    bridge = _BridgeStub(config=config, credential=credential or _BasicCredentialStub())
    backend = ImapChannelBackend(bridge)
    backend.test_pages = AdapterPages(backend)
    return backend


def _drain(backend: ImapChannelBackend) -> list[Any]:
    """Drain the backend the way Channel.sync does, collecting every message."""

    collected: list[Any] = []
    while batch := backend.test_pages.next_batch():
        collected.extend(batch)
    return collected


def test_web_sample_selection_limit_matches_backend() -> None:
    """The preview's selection cap must stay within the import owner's bound."""

    action = Path(__file__).parents[1] / "src/angee/messaging_integrate_imap/web/src/ImportImapSampleAction.tsx"
    assert f"const IMAP_SAMPLE_LIMIT = {MAX_SAMPLE_MESSAGES};" in action.read_text()


def test_sample_preview_is_bounded_readonly_and_keeps_cursor(imap_server: ImapServer) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml(subject="A"), _eml(subject="B"), _eml(subject="C"))})
    backend = _backend()
    backend.test_pages = AdapterPages(backend, cursors={"INBOX": {"uidvalidity": 100, "last_uid": 1}})

    result = backend.preview_sample(
        ImapSamplePreviewRequest(
            mailbox="INBOX",
            since=date(2026, 7, 1),
            before=date(2026, 8, 1),
            limit=2,
        )
    )

    assert [message.uid for message in result.messages] == [3, 2]
    assert result.uidvalidity == 100
    assert result.upper_uid == result.total_count == 3
    assert result.next_before_uid == 2
    assert imap_server.searches == [("INBOX", ["UID", "1:3", "SINCE", date(2026, 7, 1), "BEFORE", date(2026, 8, 1)])]
    assert all(b"BODY.PEEK[]" not in fields for _, _, fields in imap_server.fetches)
    assert backend.test_pages.cursors == {"INBOX": {"uidvalidity": 100, "last_uid": 1}}
    assert len(imap_server.logins) == imap_server.logouts == 1


def test_sample_preview_pages_one_all_dates_snapshot_and_excludes_new_mail(
    imap_server: ImapServer,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(*(_eml(subject=str(index)) for index in range(1, 6)))})
    backend = _backend()

    first = backend.preview_sample(
        ImapSamplePreviewRequest(
            mailbox="INBOX",
            since=None,
            before=None,
            all_dates=True,
            limit=2,
        )
    )
    imap_server.folders["INBOX"]["messages"][6] = {"raw": _eml(subject="new")}
    second = backend.preview_sample(
        ImapSamplePreviewRequest(
            mailbox="INBOX",
            since=None,
            before=None,
            all_dates=True,
            limit=2,
            uidvalidity=first.uidvalidity,
            upper_uid=first.upper_uid,
            before_uid=first.next_before_uid,
            total_count=first.total_count,
        )
    )
    third = backend.preview_sample(
        ImapSamplePreviewRequest(
            mailbox="INBOX",
            since=None,
            before=None,
            all_dates=True,
            limit=2,
            uidvalidity=second.uidvalidity,
            upper_uid=second.upper_uid,
            before_uid=second.next_before_uid,
            total_count=second.total_count,
        )
    )

    assert first.total_count == second.total_count == third.total_count == 5
    assert first.upper_uid == second.upper_uid == third.upper_uid == 5
    assert [message.uid for message in first.messages] == [5, 4]
    assert [message.uid for message in second.messages] == [3, 2]
    assert [message.uid for message in third.messages] == [1]
    assert first.next_before_uid == 4
    assert second.next_before_uid == 2
    assert third.next_before_uid is None
    assert imap_server.searches == [("INBOX", ["UID", f"1:{upper}"]) for upper in (5, 3, 1)]
    assert not any(backend.test_pages.cursors.values())


def test_sample_preview_rejects_still_present_unanswered_header(
    imap_server: ImapServer,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml(subject="A"), _eml(subject="B"))})
    backend = _backend()
    imap_server.withheld.add(2)

    with pytest.raises(ImapError, match="still contains UID"):
        backend.preview_sample(
            ImapSamplePreviewRequest(
                mailbox="INBOX",
                since=None,
                before=None,
                all_dates=True,
                limit=2,
            )
        )

    assert ("INBOX", ["UID", "2"]) in imap_server.searches
    assert not any(backend.test_pages.cursors.values())


@pytest.mark.parametrize("expunged", [(3,), (3, 2)])
def test_sample_preview_confirmed_expunges_reduce_retained_count_and_advance(
    imap_server: ImapServer,
    expunged: tuple[int, ...],
) -> None:
    imap_server.folders.update({"INBOX": mailbox(*(_eml(subject=str(uid)) for uid in range(1, 6)))})
    backend = _backend()
    request = ImapSamplePreviewRequest(mailbox="INBOX", all_dates=True, limit=2)
    first = backend.preview_sample(request)
    imap_server.before["fetch"] = lambda uids, data: imap_server.expunge("INBOX", *expunged)
    second = backend.preview_sample(
        request.model_copy(
            update={
                "uidvalidity": first.uidvalidity,
                "upper_uid": first.upper_uid,
                "before_uid": first.next_before_uid,
                "total_count": first.total_count,
            }
        )
    )
    third = backend.preview_sample(
        request.model_copy(
            update={
                "uidvalidity": second.uidvalidity,
                "upper_uid": second.upper_uid,
                "before_uid": second.next_before_uid,
                "total_count": second.total_count,
            }
        )
    )

    assert [message.uid for message in second.messages] == [uid for uid in (3, 2) if uid not in expunged]
    assert second.total_count == third.total_count == 5 - len(expunged)
    assert second.next_before_uid == 2
    assert [message.uid for message in third.messages] == [1]
    assert third.next_before_uid is None
    assert not any(backend.test_pages.cursors.values())


def test_sample_preview_date_window_continuation_preserves_server_search_scope(
    imap_server: ImapServer,
) -> None:
    folder = mailbox(*(_eml(subject=str(uid)) for uid in range(1, 7)))
    folder["messages"][1]["internal_date"] = datetime(2026, 6, 30, tzinfo=UTC)
    folder["messages"][6]["internal_date"] = datetime(2026, 8, 1, tzinfo=UTC)
    imap_server.folders.update({"INBOX": folder})
    backend = _backend()
    request = ImapSamplePreviewRequest(mailbox="INBOX", since=date(2026, 7, 1), before=date(2026, 8, 1), limit=2)

    first = backend.preview_sample(request)
    second = backend.preview_sample(
        request.model_copy(
            update={
                "uidvalidity": first.uidvalidity,
                "upper_uid": first.upper_uid,
                "before_uid": first.next_before_uid,
                "total_count": first.total_count,
            }
        )
    )

    assert [message.uid for message in first.messages] == [5, 4]
    assert [message.uid for message in second.messages] == [3, 2]
    assert first.total_count == second.total_count == 4
    assert second.next_before_uid is None
    assert imap_server.searches == [
        ("INBOX", ["UID", f"1:{upper}", "SINCE", request.since, "BEFORE", request.before]) for upper in (6, 3)
    ]


@pytest.mark.parametrize("changed_epoch", [False, True])
def test_sample_preview_rejects_unavailable_snapshot_or_changed_continuation_epoch(
    imap_server: ImapServer,
    changed_epoch: bool,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml(), _eml())})
    backend = _backend()
    request = ImapSamplePreviewRequest(mailbox="INBOX", all_dates=True, limit=1)
    first = backend.preview_sample(request)
    if changed_epoch:
        imap_server.folders["INBOX"]["uidvalidity"] += 1
    else:
        imap_server.folders["INBOX"]["messages"].pop(2)
    searched = len(imap_server.searches)

    message = "UID identity changed" if changed_epoch else "snapshot is no longer available"
    with pytest.raises(ValidationError, match=message):
        backend.preview_sample(
            request.model_copy(
                update={
                    "uidvalidity": first.uidvalidity,
                    "upper_uid": first.upper_uid,
                    "before_uid": first.next_before_uid,
                    "total_count": first.total_count,
                }
            )
        )

    assert len(imap_server.searches) == searched
    assert not any(backend.test_pages.cursors.values())
    assert imap_server.logouts == 2


@pytest.mark.parametrize("operation", ["preview", "prepare", "sync"])
def test_missing_uidnext_is_an_actionable_imap_error(
    imap_server: ImapServer,
    operation: str,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml())})
    backend = _backend()
    imap_server.reports_uidnext = False
    with pytest.raises(ImapError, match="did not report a valid UIDNEXT.*Check the server's IMAP STATUS support"):
        if operation == "preview":
            backend.preview_sample(ImapSamplePreviewRequest(mailbox="INBOX", all_dates=True))
        elif operation == "prepare":
            backend.prepare_new_mail_boundary()
        else:
            backend.test_pages.next_batch()
    assert all(cursor == {} for cursor in backend.test_pages.cursors.values())
    backend.close()


def test_sample_fetch_pins_uids_and_preserves_unread_flags(imap_server: ImapServer) -> None:
    folder = mailbox(_eml(message_id="a@sample", subject="A"), _eml(message_id="b@sample", subject="B"))
    folder["messages"][2]["flags"] = ()
    imap_server.folders.update({"INBOX": folder})
    backend = _backend()

    messages, imported, unchanged = backend.fetch_sample(mailbox="INBOX", uidvalidity=100, uids=[2, 99])

    assert [message.subject for message in messages] == ["B"]
    assert imported == [2] and unchanged
    assert folder["messages"][2]["flags"] == ()
    assert not any(backend.test_pages.cursors.values())
    assert imap_server.searches == []
    assert all(set(uids) <= {2, 99} for _, uids, _ in imap_server.fetches)
    assert len(imap_server.logins) == imap_server.logouts == 1


def test_sample_rejects_changed_mailbox_epoch_before_body_fetch(imap_server: ImapServer) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml(), uidvalidity=200)})
    backend = _backend()
    with pytest.raises(ValidationError, match="UID identity changed"):
        backend.fetch_sample(mailbox="INBOX", uidvalidity=100, uids=[1])
    assert imap_server.fetches == []
    assert not any(backend.test_pages.cursors.values())
    assert imap_server.logouts == 1


def test_sample_rejects_unbounded_requests_before_connect(imap_server: ImapServer) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml())})
    backend = _backend()
    with pytest.raises(ValidationError, match="sample limit"):
        backend.preview_sample(
            ImapSamplePreviewRequest(
                mailbox="INBOX",
                since=date(2026, 7, 1),
                before=date(2026, 8, 1),
                limit=51,
            )
        )
    with pytest.raises(ValidationError, match="one year"):
        backend.preview_sample(
            ImapSamplePreviewRequest(
                mailbox="INBOX",
                since=date(2024, 7, 1),
                before=date(2026, 8, 1),
            )
        )
    with pytest.raises(ValidationError, match="Do not combine all-dates"):
        backend.preview_sample(
            ImapSamplePreviewRequest(
                mailbox="INBOX",
                since=date(2026, 7, 1),
                before=date(2026, 8, 1),
                all_dates=True,
            )
        )
    with pytest.raises(ValidationError, match="Preview this mailbox scope again"):
        backend.preview_sample(
            ImapSamplePreviewRequest(
                mailbox="INBOX",
                since=None,
                before=None,
                all_dates=True,
                uidvalidity=100,
            )
        )
    with pytest.raises(ValidationError, match="positive message UIDs"):
        backend.fetch_sample(mailbox="INBOX", uidvalidity=100, uids=list(range(1, 52)))
    assert imap_server.logins == []


def test_backfill_pages_in_batches_and_sets_the_cursor(imap_server: ImapServer) -> None:
    """Initial sync drains a folder in batch_size pages and records its watermark."""

    imap_server.folders.update(
        {
            "INBOX": mailbox(
                _eml(message_id="<a@x>", subject="A"),
                _eml(message_id="<b@x>", subject="B"),
                _eml(message_id="<c@x>", subject="C"),
            ),
            "Junk": mailbox(_eml(message_id="<spam@x>"), flags=(b"\\Junk",)),
        }
    )
    backend = _backend(config={"batch_size": 2})

    first = backend.test_pages.next_batch()
    second = backend.test_pages.next_batch()
    third = backend.test_pages.next_batch()

    assert [message.subject for message in first] == ["A", "B"]
    assert [message.subject for message in second] == ["C"]
    assert third == []
    assert imap_server.selects == ["INBOX"]  # Junk never opened
    assert backend.test_pages.cursors["INBOX"] == {"uidvalidity": 100, "last_uid": 3}
    assert imap_server.logouts == 1
    assert imap_server.logins == [("login", "ada@example.com", "pw")]


def test_present_unanswered_uid_fails_before_cursor_advance(imap_server: ImapServer) -> None:
    """A transient FETCH omission remains retryable while the UID still exists."""

    imap_server.folders.update({"INBOX": mailbox(_eml(subject="A"), _eml(subject="B"))})
    backend = _backend()
    imap_server.withheld.add(2)

    with pytest.raises(ImapError, match="still contains UID"):
        backend.test_pages.next_batch()

    assert backend.test_pages.cursors == {"INBOX": {}}
    assert ("INBOX", ["UID", "2"]) in imap_server.searches
    assert imap_server.selects == ["INBOX"]


def test_confirmed_expunged_uid_allows_cursor_advance(imap_server: ImapServer) -> None:
    """A UID expunged after discovery does not pin the mailbox watermark forever."""

    imap_server.folders.update({"INBOX": mailbox(_eml(subject="A"), _eml(subject="B"))})
    backend = _backend()
    imap_server.before["fetch"] = lambda uids, data: imap_server.expunge("INBOX", 2)

    messages = backend.test_pages.next_batch()

    assert [message.subject for message in messages] == ["A"]
    assert backend.test_pages.cursors["INBOX"] == {"uidvalidity": 100, "last_uid": 2}
    assert ("INBOX", ["UID", "2"]) in imap_server.searches


_MAILBOX_EVENTS = st.lists(
    st.one_of(
        st.tuples(st.sampled_from(["deliver", "deliver_during_fetch", "sync"]), st.just(0)),
        st.tuples(st.sampled_from(["expunge", "expunge_during_fetch", "withhold"]), st.integers(0, 15)),
    ),
    max_size=25,
)


@settings(max_examples=150, deadline=None)
@given(events=_MAILBOX_EVENTS)
@example(events=[("deliver", 0), ("deliver", 0), ("deliver", 0), ("withhold", 2), ("sync", 0), ("sync", 0)])
@example(events=[("deliver", 0), ("deliver", 0), ("expunge_during_fetch", 1), ("deliver_during_fetch", 0), ("sync", 0)])
def test_the_cursor_never_passes_an_unconsumed_uid(events: list[tuple[str, int]]) -> None:
    """Whatever the order of arrivals, expunges and unanswered FETCHes, no present UID at or
    below the committed cursor was skipped, and a clean sync then consumes every message."""

    server = ImapServer({"INBOX": mailbox()})
    stream = SimpleNamespace(partition="INBOX", generation=1, cursor={})
    consumed: set[int] = set()

    def sync() -> None:
        backend = _backend(config={"batch_size": 2})
        try:
            while True:
                page = backend.extract(stream, 2)
                consumed.update(int(message.metadata["uid"]) for message in page.records)
                stream.cursor = page.cursor
                if page.exhausted:
                    break
        except ImapError:
            pass  # the run fails and its last committed cursor stays
        finally:
            backend.close()
            server.withheld.clear()  # an unanswered FETCH is a transient server hiccup

    def during_fetch(action: Callable[[], object]) -> None:
        def run(uids: list[int], data: list[bytes]) -> None:
            del server.before["fetch"]
            action()

        server.before["fetch"] = run

    with serve(server):
        for event, pick in events:
            present = server.uids("INBOX")
            chosen = present[pick % len(present)] if present else None
            arrival = partial(server.deliver, "INBOX", _eml(message_id=f"<{len(consumed)}-{pick}@x>"))
            if event == "deliver":
                arrival()
            elif event == "deliver_during_fetch":
                during_fetch(arrival)
            elif event == "sync":
                sync()
            elif chosen is not None and event == "expunge":
                server.expunge("INBOX", chosen)
            elif chosen is not None and event == "expunge_during_fetch":
                during_fetch(partial(server.expunge, "INBOX", chosen))
            elif chosen is not None:
                server.withheld.add(chosen)
            last_uid = int(stream.cursor.get("last_uid", 0))
            assert {uid for uid in server.uids("INBOX") if uid <= last_uid} <= consumed
        server.before.clear()
        server.withheld.clear()
        sync()
    assert set(server.uids("INBOX")) <= consumed


@pytest.mark.parametrize("operation", ["sync", "preview", "sample"])
def test_unanswered_uid_confirmation_rejects_changed_epoch(
    imap_server: ImapServer,
    operation: str,
) -> None:
    """Existence confirmation cannot cross into a regenerated UID namespace."""

    imap_server.folders.update({"INBOX": mailbox(_eml(subject="A"), _eml(subject="B"))})
    backend = _backend()
    imap_server.withheld.add(2)
    imap_server.after["fetch"] = lambda uids, data: imap_server.folders["INBOX"].update(uidvalidity=200)

    if operation == "sync":
        with pytest.raises(CursorInvalid):
            backend.test_pages.next_batch()
        assert backend.test_pages.cursors == {"INBOX": {}}
    else:
        with pytest.raises(ValidationError, match="UID identity changed"):
            if operation == "preview":
                backend.preview_sample(ImapSamplePreviewRequest(mailbox="INBOX", all_dates=True))
            else:
                backend.fetch_sample(mailbox="INBOX", uidvalidity=100, uids=[1, 2])
        assert backend.test_pages.cursors == {}
        assert imap_server.logouts == 1

    assert imap_server.selects == ["INBOX"]


def test_gmail_all_mail_special_use_wins_folder_selection(imap_server: ImapServer) -> None:
    """A ``\\All`` folder already holds every non-junk message, so it syncs alone."""

    imap_server.folders.update(
        {
            "INBOX": mailbox(_eml(message_id="<i@x>")),
            "[Gmail]/All Mail": mailbox(_eml(message_id="<i@x>"), _eml(message_id="<s@x>"), flags=(b"\\All",)),
            "[Gmail]/Trash": mailbox(flags=(b"\\Trash",)),
        }
    )
    backend = _backend()

    messages = _drain(backend)

    assert {message.metadata["mailbox"] for message in messages} == {"[Gmail]/All Mail"}
    assert imap_server.selects == ["[Gmail]/All Mail"]


def test_each_mailbox_fetches_from_its_own_selectedmailbox(imap_server: ImapServer) -> None:
    """FETCH is stateful: every chunk pins its mailbox, so multi-folder mail never crosses.

    Discovery leaves the last-planned folder selected; without the per-chunk
    re-select, the first folder's UIDs would fetch from the wrong mailbox —
    mislabelling one folder's mail and silently skipping the other's while its
    cursor still advanced.
    """

    imap_server.folders.update(
        {
            "INBOX": mailbox(
                _eml(message_id="<i1@x>", subject="I1"),
                _eml(message_id="<i2@x>", subject="I2"),
            ),
            "Archive": mailbox(_eml(message_id="<a1@x>", subject="A1")),
        }
    )
    backend = _backend(config={"mailboxes": ["INBOX", "Archive"]})

    messages = _drain(backend)

    assert {(message.metadata["mailbox"], message.external_id) for message in messages} == {
        ("INBOX", "i1@x"),
        ("INBOX", "i2@x"),
        ("Archive", "a1@x"),
    }
    assert backend.test_pages.cursors == {
        "INBOX": {"uidvalidity": 100, "last_uid": 2},
        "Archive": {"uidvalidity": 100, "last_uid": 1},
    }


def test_operator_skip_outranks_the_all_mail_preference(imap_server: ImapServer) -> None:
    """A configured skip of the ``\\All`` folder falls back to normal selection."""

    imap_server.folders.update(
        {
            "INBOX": mailbox(_eml(message_id="<i@x>")),
            "[Gmail]/All Mail": mailbox(_eml(message_id="<i@x>"), flags=(b"\\All",)),
        }
    )
    backend = _backend(config={"skip_mailboxes": ["[Gmail]/All Mail"]})

    messages = _drain(backend)

    assert {message.metadata["mailbox"] for message in messages} == {"INBOX"}


def test_explicit_mailboxes_config_wins_verbatim(imap_server: ImapServer) -> None:
    """An operator's mailboxes list overrides special-use and skip heuristics."""

    imap_server.folders.update(
        {
            "INBOX": mailbox(_eml(message_id="<i@x>")),
            "Archive/2026": mailbox(_eml(message_id="<old@x>")),
        }
    )
    backend = _backend(config={"mailboxes": ["Archive/2026"]})

    messages = _drain(backend)

    assert [message.metadata["mailbox"] for message in messages] == ["Archive/2026"]


def test_incremental_run_prescreens_with_uidnext(imap_server: ImapServer) -> None:
    """An unchanged folder costs one STATUS — no SELECT, no SEARCH, no FETCH."""

    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<a@x>"), _eml(message_id="<b@x>"))})
    backend = _backend()
    backend.test_pages = AdapterPages(backend, cursors={"INBOX": {"uidvalidity": 100, "last_uid": 2}})

    assert backend.test_pages.next_batch() == []
    assert imap_server.status_calls == ["INBOX"]
    assert imap_server.selects == []
    assert imap_server.fetches == []


def test_incremental_run_fetches_only_new_uids(imap_server: ImapServer) -> None:
    """New mail beyond the watermark is fetched; old UIDs never re-download."""

    imap_server.folders.update(
        {
            "INBOX": mailbox(
                _eml(message_id="<a@x>", subject="A"),
                _eml(message_id="<b@x>", subject="B"),
                _eml(message_id="<c@x>", subject="C"),
            )
        }
    )
    backend = _backend()
    backend.test_pages = AdapterPages(backend, cursors={"INBOX": {"uidvalidity": 100, "last_uid": 2}})

    messages = _drain(backend)

    assert [message.subject for message in messages] == ["C"]
    assert imap_server.searches == [("INBOX", ["UID", "3:3"])]
    assert backend.test_pages.cursors["INBOX"]["last_uid"] == 3


def test_expunged_uid_past_the_watermark_fetches_nothing(imap_server: ImapServer) -> None:
    """A UIDNEXT past the watermark with no surviving mail there yields no refetch."""

    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<a@x>"), _eml(message_id="<b@x>"))})
    backend = _backend()
    backend.test_pages = AdapterPages(backend, cursors={"INBOX": {"uidvalidity": 100, "last_uid": 2}})
    # A UID was allocated then expunged server-side: UIDNEXT moved past the
    # watermark, so the prescreen lets the search through, and it finds nothing.
    imap_server.expunge("INBOX", imap_server.deliver("INBOX", _eml(message_id="<gone@x>")))

    assert backend.test_pages.next_batch() == []
    assert imap_server.searches == [("INBOX", ["UID", "3:3"])]
    assert imap_server.fetches == []


def test_a_full_mailbox_is_searched_in_bounded_uid_windows(imap_server: ImapServer) -> None:
    """No SEARCH answer outgrows imaplib's line limit, however many UIDs a mailbox holds."""

    uids = (1, WINDOW + 1, 2 * WINDOW + 1)
    imap_server.folders["INBOX"] = {"flags": (), "uidvalidity": 100, "messages": {
        uid: {"raw": _eml(message_id=f"<{uid}@x>", subject=str(uid))} for uid in uids
    }}

    messages = _drain(_backend())
    preview = _backend().preview_sample(ImapSamplePreviewRequest(mailbox="INBOX", all_dates=True, limit=5))

    windows = [("INBOX", ["UID", f"{uid}:{min(uid + WINDOW - 1, uids[-1])}"]) for uid in uids]
    assert [message.subject for message in messages] == [str(uid) for uid in uids]
    assert [message.uid for message in preview.messages] == list(reversed(uids))
    assert imap_server.searches == windows + windows


def test_a_server_failure_names_the_mailbox_and_step_but_not_the_reply(imap_server: ImapServer) -> None:
    """Sync telemetry says where the server failed; its raw reply stays in the worker log."""

    imap_server.folders.update({"INBOX": mailbox(_eml())})
    backend = _backend()

    imap_server.refuse("search", IMAPClientError("command: SEARCH => got more than 1000000 bytes"))
    with pytest.raises(ImapError) as refused:
        backend.test_pages.next_batch()

    assert refused.value.public_message == (
        "The IMAP server refused a command, or sent a reply that could not be read, while listing "
        "messages in mailbox 'INBOX'. The worker log has the server's reply."
    )
    assert isinstance(refused.value.__cause__, IMAPClientError)


def test_uidvalidity_change_resets_the_folder_cursor(imap_server: ImapServer) -> None:
    """A regenerated mailbox refetches from scratch under its new UID space."""

    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<a@x>", subject="A"), uidvalidity=777)})
    backend = _backend()
    backend.test_pages = AdapterPages(backend, cursors={"INBOX": {"uidvalidity": 100, "last_uid": 50}})

    with pytest.raises(CursorInvalid) as invalid:
        _drain(backend)
    stream = backend.test_pages.rows["INBOX"]
    stream.generation += 1
    stream.cursor = invalid.value.cursor
    messages = _drain(backend)

    assert [message.subject for message in messages] == ["A"]
    assert imap_server.searches == [("INBOX", ["UID", "1:1"])]
    assert backend.test_pages.cursors["INBOX"] == {"uidvalidity": 777, "last_uid": 1}


def test_oversized_message_lands_header_only(imap_server: ImapServer) -> None:
    """A message over the size cap keeps its envelope with a truncation marker."""

    big = _eml(message_id="<big@x>", subject="Big", body="x" * 2000)
    small = _eml(message_id="<small@x>", subject="Small")
    imap_server.folders.update({"INBOX": mailbox(small, big)})
    backend = _backend(config={"max_message_bytes": 1000})

    messages = {message.subject: message for message in _drain(backend)}

    assert messages["Small"].metadata.get("truncated_bytes") is None
    assert messages["Big"].metadata["truncated_bytes"] == len(big)
    assert messages["Big"].body is None
    assert messages["Big"].external_id == "big@x"


def test_poison_message_lands_through_the_fallback_envelope(
    imap_server: ImapServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message the MIME layer rejects still lands with its raw bytes attached."""

    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<p@x>", subject="Poison"))})
    backend = _backend()

    def explode(raw: bytes, **kwargs: Any) -> Any:
        raise ValueError("unparseable")

    monkeypatch.setattr("angee.messaging_integrate_imap.backend.parse_message", explode)
    messages = _drain(backend)

    assert len(messages) == 1
    assert messages[0].metadata["parse_error"] == "ValueError: unparseable"
    assert messages[0].body.content.startswith(b"From:")


@pytest.mark.parametrize("failure", [AttributeError("folded header"), AssertionError("parser invariant")])
def test_unlisted_parser_failure_lands_the_fallback_and_keeps_the_page(
    imap_server: ImapServer,
    monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    """An error outside MIME_ERRORS isolates one message instead of stalling the mailbox."""

    imap_server.folders.update({"INBOX": mailbox(
        _eml(message_id="<poison@x>", subject="Poison"), _eml(message_id="<next@x>", subject="Next"),
    )})
    backend = _backend()
    parse = imap_backend.parse_message

    def explode_once(raw: bytes, **kwargs: Any) -> Any:
        if b"<poison@x>" in raw:
            raise failure
        return parse(raw, **kwargs)

    monkeypatch.setattr(imap_backend, "parse_message", explode_once)
    messages = {message.external_id: message for message in _drain(backend)}

    assert set(messages) == {"poison@x", "next@x"}
    assert messages["poison@x"].metadata["parse_error"] == f"{type(failure).__name__}: {failure}"
    assert messages["poison@x"].body.content.startswith(b"From:")
    assert "parse_error" not in messages["next@x"].metadata


def test_oauth_credential_refreshes_then_authenticates_xoauth2(imap_server: ImapServer) -> None:
    """An OAuth channel freshens its token and logs in over XOAUTH2."""

    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<a@x>"))})
    credential = _OAuthCredentialStub()

    _drain(_backend(config={"username": "ada@example.com"}, credential=credential))

    assert credential.freshened == 1
    assert imap_server.logins == [("oauth2", "ada@example.com", "token-123")]


def test_unusable_configuration_fails_loudly(imap_server: ImapServer) -> None:
    """Missing host, unknown security mode, and unsupported credentials all raise."""

    imap_server.folders.update({"INBOX": mailbox()})

    backend = _backend()
    backend.bridge.config["host"] = ""
    with pytest.raises(ImapError, match="host"):
        backend.test_pages.next_batch()

    backend = _backend(config={"security": "carrier-pigeon"})
    with pytest.raises(ImapError, match="security"):
        backend.test_pages.next_batch()

    class _SshCredential:
        kind = CredentialKind.SSH_KEY
        external_account = None

    backend = _backend(credential=_SshCredential())
    with pytest.raises(ImapError, match="credential"):
        backend.test_pages.next_batch()

    backend = _backend()
    backend.bridge.credential = None
    with pytest.raises(ImapError, match="credential"):
        backend.test_pages.next_batch()


def test_host_is_judged_by_the_outbound_address_owner(imap_server: ImapServer) -> None:
    """Private self-hosted mail is allowed; metadata/link-local escapes never are."""

    imap_server.folders.update({"INBOX": mailbox()})
    backend = _backend(config={"host": "169.254.169.254"})
    with pytest.raises(ImapError, match="forbidden"):
        backend.test_pages.next_batch()

    # A private (RFC 1918) host is the legitimate self-hosted case and connects.
    backend = _backend(config={"host": "10.0.0.4"})
    assert backend.test_pages.next_batch() == []
    assert imap_server.logins  # the private host authenticated


def test_login_refusal_names_the_account_and_host(imap_server: ImapServer) -> None:
    """A rejected password surfaces as an operator-safe ImapError, never a raw LoginError."""

    imap_server.folders.update({"INBOX": mailbox()})

    imap_server.refuse("login", LoginError("[AUTHENTICATIONFAILED] Authentication failed. Private server detail: pw"))
    backend = _backend(config={"host": "10.0.0.4"})

    with pytest.raises(ImapError) as raised:
        backend.test_pages.next_batch()

    assert raised.value.public_message == (
        "IMAP login failed for 'ada@example.com' at 10.0.0.4. Check the account credentials."
    )
    assert "pw" not in raised.value.public_message
    assert "Private server detail" not in raised.value.public_message
    assert isinstance(raised.value.__cause__, LoginError)
    assert imap_server.logouts == 1
    assert backend._client is None


def test_transport_failure_while_dialing_is_an_imap_error(imap_server: ImapServer) -> None:
    """A refused socket at dial time reports the host, not a bare OSError."""

    imap_server.folders.update({"INBOX": mailbox()})

    imap_server.refuse("connect", ConnectionRefusedError("connection refused"))
    backend = _backend(config={"host": "10.0.0.4"})

    with pytest.raises(ImapError) as raised:
        backend.test_pages.next_batch()
    assert raised.value.public_message == (
        "IMAP connection to 10.0.0.4 failed. Check the host, port, and security settings."
    )
    assert isinstance(raised.value.__cause__, ConnectionRefusedError)


def test_connection_test_signs_in_and_logs_out(imap_server: ImapServer) -> None:
    """The connection test is one login round-trip with no mailbox touched."""

    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<a@x>"))})
    backend = _backend(config={"host": "10.0.0.4"})

    message = backend.test_connection()

    assert message == "Signed in to 10.0.0.4 as ada@example.com."
    assert imap_server.logins == [("login", "ada@example.com", "pw")]
    assert imap_server.logouts == 1
    assert imap_server.selects == []
    assert imap_server.fetches == []


def test_transient_transport_error_reconnects_and_retries_once(imap_server: ImapServer) -> None:
    """One dropped connection mid-fetch reconnects and completes the batch."""

    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<a@x>", subject="A"))})

    def drop_connection(uids: list[int], data: list[bytes]) -> None:
        del imap_server.before["fetch"]
        raise ConnectionResetError("gone")

    imap_server.before["fetch"] = drop_connection
    backend = _backend()

    messages = _drain(backend)

    assert [message.subject for message in messages] == ["A"]
    assert len(imap_server.logins) == 2  # the reconnect re-authenticated


def test_ssl_context_is_the_stdlib_default(imap_server: ImapServer) -> None:
    """TLS trust rides the runtime's default context, never a hand-rolled one."""

    imap_server.folders.update({"INBOX": mailbox()})
    _backend().test_pages.next_batch()

    [dial] = imap_server.connections
    assert isinstance(dial["ssl_context"], ssl.SSLContext)
    assert dial["ssl"] is True


def test_expired_imap_budget_does_not_connect_or_advance(
    imap_server: ImapServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml())})
    backend = _backend()
    monkeypatch.setattr(imap_backend, "monotonic", lambda: 10.0)
    reports: list[tuple[str, dict[str, Any]]] = []
    bridge = SimpleNamespace(report_progress=lambda stage, **values: reports.append((stage, values)))
    monkeypatch.setattr(imap_backend, "current_progress_bridge", lambda: bridge)
    stream = SimpleNamespace(partition="INBOX", generation=1, cursor={"uidvalidity": 100, "last_uid": 0})

    assert backend.streams(deadline=10) == ()
    assert reports[0][0] == "syncing"
    assert reports[0][1]["details"]["budget_exhausted"] is True
    assert "discovery unfinished" in reports[0][1]["message"]
    page = backend.extract(stream, 10, deadline=10)

    assert not page.records and not page.exhausted
    assert page.cursor == stream.cursor
    assert imap_server.connections == []


def test_imap_search_budget_stop_rebuilds_the_plan(imap_server: ImapServer, monkeypatch: pytest.MonkeyPatch) -> None:
    imap_server.folders["INBOX"] = {"flags": (), "uidvalidity": 100, "messages": {
        1: {"raw": _eml(subject="A")}, WINDOW + 1: {"raw": _eml(subject="B")},  # two SEARCH windows
    }}
    backend = _backend()
    clock = [0.0]
    monkeypatch.setattr(imap_backend, "monotonic", lambda: clock[0])
    imap_server.after["search"] = lambda criteria: clock.__setitem__(0, 10)
    stream = SimpleNamespace(partition="INBOX", generation=1, cursor={})
    page = backend.extract(stream, 10, deadline=10)
    assert not page.records and not page.exhausted and page.cursor == {}
    assert len(imap_server.searches) == 1 and imap_server.fetches == []
    assert imap_server.shutdowns == 1 and imap_server.logouts == 0

    resumed = backend.extract(stream, 10, deadline=20)
    assert [message.subject for message in resumed.records] == ["A", "B"]
    assert resumed.cursor == {"uidvalidity": 100, "last_uid": WINDOW + 1}
    assert len(imap_server.searches) == 3
    backend.close()


def test_imap_connection_and_commands_use_remaining_native_timeout(
    imap_server: ImapServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml())})
    backend = _backend(config={"timeout": 4})
    clock = [7.0]
    monkeypatch.setattr(imap_backend, "monotonic", lambda: clock[0])
    imap_server.after["folder_status"] = lambda name: clock.__setitem__(0, 9)
    stream = SimpleNamespace(partition="INBOX", generation=1, cursor={})
    page = backend.extract(stream, 10, deadline=10)
    backend.close()

    assert len(page.records) == 1
    assert [dial["timeout"] for dial in imap_server.connections] == [3.0]
    assert imap_server.read_timeouts[0] == 3.0
    assert imap_server.read_timeouts[-1] == 1.0  # LOGOUT is bounded too.
    assert max(imap_server.read_timeouts) == 3.0
    assert imap_server.logouts == 1 and imap_server.shutdowns == 0


@pytest.mark.parametrize("expired", [False, True])
def test_imap_timeout_retries_only_with_budget(
    imap_server: ImapServer,
    monkeypatch: pytest.MonkeyPatch,
    expired: bool,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml())})
    backend = _backend()
    clock = [0.0]
    monkeypatch.setattr(imap_backend, "monotonic", lambda: clock[0])

    def time_out(uids: list[int], data: list[bytes]) -> None:
        del imap_server.before["fetch"]
        clock[0] = 10 if expired else 1
        raise TimeoutError("socket read timed out")

    imap_server.before["fetch"] = time_out
    stream = SimpleNamespace(partition="INBOX", generation=1, cursor={})
    page = backend.extract(stream, 10, deadline=10)
    backend.close()

    if expired:
        assert not page.records and not page.exhausted and page.cursor == {}
        assert len(imap_server.logins) == imap_server.shutdowns == 1 and imap_server.fetches == []
        assert imap_server.logouts == 0
    else:
        assert len(page.records) == 1 and page.exhausted
        assert len(imap_server.logins) == 2


def test_imap_protocol_failure_is_not_disguised_as_budget_stop(
    imap_server: ImapServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml())})
    backend = _backend()
    clock = [0.0]
    monkeypatch.setattr(imap_backend, "monotonic", lambda: clock[0])

    def refused(uids: list[int], data: list[bytes]) -> None:
        clock[0] = 10
        raise IMAPClientError("refused")

    imap_server.before["fetch"] = refused
    stream = SimpleNamespace(partition="INBOX", generation=1, cursor={})
    with pytest.raises(ImapError, match="refused a command"):
        backend.extract(stream, 10, deadline=10)
    backend.close()
    assert imap_server.shutdowns == 1


@pytest.mark.parametrize("phase", ["fetch", "parse", "logout"])
def test_imap_worker_cancellation_propagates(
    imap_server: ImapServer,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml())})
    backend = _backend()
    monkeypatch.setattr(imap_backend, "monotonic", lambda: 0.0)

    def cancelled(*args: Any, **kwargs: Any) -> None:
        raise SoftTimeLimitExceeded

    if phase == "parse":
        monkeypatch.setattr(imap_backend, "parse_message", cancelled)
    else:
        imap_server.refuse(phase, SoftTimeLimitExceeded())
    stream = SimpleNamespace(partition="INBOX", generation=1, cursor={})
    with pytest.raises(SoftTimeLimitExceeded):
        backend.extract(stream, 10, deadline=10)
        backend.close()
    if phase == "logout":
        assert imap_server.shutdowns == 1
        assert backend._client is None
    else:
        assert stream.cursor == {}
        backend.close()


@pytest.mark.parametrize("phase", ["body", "header", "html"])
def test_mime_fallbacks_propagate_worker_cancellation(monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    def cancelled(*args: Any, **kwargs: Any) -> None:
        raise SoftTimeLimitExceeded

    with pytest.raises(SoftTimeLimitExceeded):
        if phase == "body":
            monkeypatch.setattr(imap_parser.MIMEPart, "get_content", cancelled)
            parse_message(_eml(), mailbox="INBOX", uid=1, uidvalidity=100)
        elif phase == "header":
            monkeypatch.setattr(imap_parser, "decode_header", cancelled)
            fallback_message(_eml(), mailbox="INBOX", uid=1, uidvalidity=100)
        else:
            monkeypatch.setattr(imap_parser.HTMLParser, "feed", cancelled)
            html_to_text("<p>Hello</p>")


# --- end to end: Channel.run_sync over real tables ---


def _imap_channel(**config: Any) -> Any:
    """Create an IMAP Channel row with a basic-auth credential."""

    return make_integration(
        "imap",
        kind=CredentialKind.BASIC_AUTH,
        material={"username": "ada@example.com", "password": "pw"},
        model=Channel,
        backend_class="imap",
        config={"host": "192.0.2.10", **config},
    )


@pytest.mark.parametrize("change", ["rotate", "repoint"])
def test_extract_reloads_credential_between_pages(composed_tables: None, change: str) -> None:
    """A reused backend observes another worker's secret or credential-FK edit."""

    with system_context(reason="tests.imap.credential_freshness"):
        channel = _imap_channel()
        backend = ImapChannelBackend(channel)
        stream = SimpleNamespace(partition="INBOX", generation=1)
        backend._stream_identity = (stream.partition, stream.generation)
        backend._work = deque()
        assert backend.extract(stream, 1).exhausted
        cached = backend.bridge.credential
        assert cached.reveal()["password"] == "pw"

        credentials = type(cached).objects
        if change == "rotate":
            current = credentials.get(pk=cached.pk)
            current.update_material(password="rotated-password")
        else:
            current = credentials.create_local_credential(
                channel.owner,
                kind=CredentialKind.BASIC_AUTH,
                name="Replacement IMAP credential",
                material={"username": "ada@example.com", "password": "rotated-password"},
            )
            Channel._base_manager.filter(pk=channel.pk).update(credential=current)
        assert backend.bridge.credential is cached
        assert cached.reveal()["password"] == "pw"

        assert backend.extract(stream, 1).exhausted
        assert backend._credential.pk == current.pk
        assert backend._credential.reveal()["password"] == "rotated-password"


@pytest.mark.django_db(transaction=True)
def test_aborted_imap_fetch_keeps_durable_cursor_and_replays_whole_chunk(
    imap_server: ImapServer,
    composed_tables: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetched but unapplied UIDs cannot be skipped on the same adapter's next attempt."""

    imap_server.folders.update({"INBOX": mailbox(*(
        _eml(message_id=f"<deadline-{uid}@example.com>", subject=str(uid)) for uid in range(1, 4)
    ))})
    channel = _imap_channel(max_batch_bytes=1)
    clock = [0.0]
    monkeypatch.setattr(imap_backend, "monotonic", lambda: clock[0])
    imap_server.after["fetch"] = lambda uids, data: b"BODY.PEEK[]" in data and clock.__setitem__(0, 10)
    with system_context(reason="test imap aborted page replay"):
        backend = channel.backend
        stream = current_stream(channel, "messages", "INBOX", cursor={"uidvalidity": 100, "last_uid": 1})
        try:
            stopped = advance_stream(stream, backend, page_bound=2, deadline=10)
            stream.refresh_from_db()
            assert stopped.count == 0 and not stopped.exhausted
            assert stream.cursor == {"uidvalidity": 100, "last_uid": 1}
            assert Message._base_manager.count() == 0
            assert [uids for _, uids, fields in imap_server.fetches if b"BODY.PEEK[]" in fields] == [(2,)]

            resumed = advance_stream(stream, backend, page_bound=2, deadline=20)
            stream.refresh_from_db()
            assert resumed.count == 2 and resumed.exhausted
            assert stream.cursor == {"uidvalidity": 100, "last_uid": 3}
            assert set(Message._base_manager.values_list("external_id", flat=True)) == {
                "deadline-2@example.com", "deadline-3@example.com",
            }
            assert [uids for _, uids, fields in imap_server.fetches if b"BODY.PEEK[]" in fields] == [(2,), (2,), (3,)]
        finally:
            backend.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("last_uid", [1, 2])
def test_retained_imap_position_continues_without_reimport(
    imap_server: ImapServer,
    composed_tables: None, last_uid: int,
) -> None:
    """The current mailbox stream owns progress across repeated syncs."""

    imap_server.folders.update(
        {"INBOX": mailbox(*(_eml(message_id=f"<kept-{uid}@example.com>", subject=str(uid)) for uid in range(1, 4)))}
    )
    channel = _imap_channel()
    with system_context(reason="test imap retained stream position"):
        current_stream(channel, "messages", "INBOX", cursor={"uidvalidity": 100, "last_uid": last_uid})
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 3 - last_uid
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 0
        assert current_stream(channel, "messages", "INBOX").cursor == {"uidvalidity": 100, "last_uid": 3}
    assert set(Message._base_manager.values_list("external_id", flat=True)) == {
        f"kept-{uid}@example.com" for uid in range(last_uid + 1, 4)
    }


@pytest.mark.django_db(transaction=True)
def test_future_only_policy_survives_a_stream_reset(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<old@example.com>"))})
    channel = _imap_channel()
    with system_context(reason="test imap future-only policy"):
        backend = channel.backend
        try:
            backend.streams()
            identity = backend._source_identity_digest()
        finally:
            backend.close()
        channel.config = {
            **channel.config, "delivery_mode": "new_only", "source_identity": identity, "mailbox_selection": ["INBOX"],
        }
        channel.save(update_fields=["config"])
        current_stream(channel, "messages", "INBOX", cursor={"uidvalidity": 100, "last_uid": 1})
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 0
        stream = current_stream(channel, "messages", "INBOX")
        channel.refresh_from_db()
        assert channel.config["delivery_mode"] == "new_only"
        assert channel.config["source_identity"] == identity
        assert channel.config["mailbox_selection"] == ["INBOX"]
        assert stream.config == {}
        imap_server.folders["INBOX"]["uidvalidity"] = 200
        imap_server.folders["INBOX"]["messages"][2] = {"raw": _eml(message_id="<old-epoch@example.com>")}
        stream.resync_required = True
        stream.save(update_fields=["resync_required"])
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 0
        successor = current_stream(channel, "messages", "INBOX")
        assert successor.generation == stream.generation + 1
        assert successor.config == stream.config
        assert successor.cursor == {"uidvalidity": 200, "last_uid": 2}
    assert not Message._base_manager.exists()


@pytest.mark.django_db(transaction=True)
def test_new_mailbox_uses_current_policy_without_restoring_removed_settings(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<retained-inbox@example.com>"))})
    channel = _imap_channel()
    positions = {name: {"uidvalidity": 100, "last_uid": 1} for name in ("Archive", "INBOX")}
    with system_context(reason="test imap removed delivery policy"):
        channel.config = {**channel.config, "delivery_mode": "new_only"}
        channel.save(update_fields=["config"])
        for name, cursor in positions.items():
            current_stream(channel, "messages", name, cursor=cursor)
        first = open_stream(
            channel,
            "messages",
            "INBOX",
            channel.backend,
            definition=StreamDefinition(key="messages", partition="INBOX"),
        )
        assert first.cursor == positions["INBOX"]
        assert channel.config["delivery_mode"] == "new_only"
        channel.config.pop("delivery_mode")
        channel.save(update_fields=["config"])
        imap_server.folders["Archive"] = mailbox(
            _eml(message_id="<retained-archive@example.com>"),
            _eml(message_id="<archive-arrival@example.com>"),
        )
        imap_server.folders["New"] = mailbox(_eml(message_id="<new-mailbox-history@example.com>"))

        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 2

        channel.refresh_from_db()
        assert "delivery_mode" not in channel.config
        assert set(Message._base_manager.values_list("external_id", flat=True)) == {
            "archive-arrival@example.com",
            "new-mailbox-history@example.com",
        }


@pytest.mark.django_db(transaction=True)
def test_new_mail_boundary_preserves_existing_exclusion(imap_server: ImapServer, composed_tables: None) -> None:
    """A repeated starting-point action must not exclude subsequent arrivals."""

    imap_server.folders.update(
        {"INBOX": mailbox(_eml(message_id="<old@example.com>"), _eml(message_id="<new@example.com>"))}
    )
    channel = _imap_channel()
    with system_context(reason="test imap boundary replay"):
        backend = channel.backend
        try:
            backend.streams()
            identity = backend._source_identity_digest()
        finally:
            backend.close()
        channel.config = {
            **channel.config, "delivery_mode": "new_only", "source_identity": identity, "mailbox_selection": ["INBOX"],
        }
        channel.save(update_fields=["config"])
        current_stream(channel, "messages", "INBOX", cursor={"uidvalidity": 100, "last_uid": 1})
        boundary = channel.select_imap_new_mail_boundary(backend.prepare_new_mail_boundary(), backend)
        assert not boundary.changed
        assert boundary.cursors == {"INBOX": {"uidvalidity": 100, "last_uid": 1}}
        channel.refresh_from_db()
        assert channel.config["delivery_mode"] == "new_only"
        assert all(stream.config == {} for stream in boundary.streams)
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 1
    assert set(Message._base_manager.values_list("external_id", flat=True)) == {"new@example.com"}


@pytest.mark.django_db(transaction=True)
def test_channel_sync_partitions_mailboxes(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    """Independent mailbox streams retain their own cursors without replica links."""

    del composed_tables
    imap_server.folders.update(
        {
            "INBOX": mailbox(_eml(message_id="<in-1@x>", subject="One", body="Inbox body\n")),
            "Archive": mailbox(_eml(message_id="<ar-1@x>", subject="Two", body="Archive body\n")),
        }
    )
    channel = _imap_channel(sync_parallelism=2, own_addresses=["ada@example.com"])

    with system_context(reason="test imap partition drain"):
        backend = channel.backend
        try:
            definitions = backend.streams()
            assert {definition.partition for definition in definitions} == {"INBOX", "Archive"}
            stream = current_stream(channel, "messages", "Archive")
            result = advance_stream(stream, backend)
            assert result.count == 1
            assert {row.partition for row in SyncStream.objects.current_for_bridge(channel, "messages")} == {"Archive"}
        finally:
            backend.close()

        landed = channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert landed == 1
    assert Message._base_manager.count() == 2
    mailboxes = {row.partition: row.cursor for row in SyncStream.objects.current_for_bridge(channel, "messages")}
    assert set(mailboxes) == {"INBOX", "Archive"}
    assert all(int(entry["last_uid"]) >= 1 for entry in mailboxes.values())
    assert RecordLink._base_manager.count() == 0


@pytest.mark.django_db(transaction=True)
def test_channel_sync_preserves_overlong_message_id(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    """A long but valid Message-ID lands unchanged as the message/thread key."""

    del composed_tables
    long_message_id = f"outlook-{'x' * 700}@example.com"
    imap_server.folders.update(
        {"INBOX": mailbox(_eml(message_id=f"<{long_message_id}>", subject="", body="No subject.\n"))}
    )
    channel = _imap_channel(batch_size=1)

    with system_context(reason="test imap long message-id"):
        landed = channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert landed == 1
    message = Message._base_manager.get()
    assert message.external_id == long_message_id
    assert len(message.external_id) > 512
    assert message.thread.external_id == f"msg:{long_message_id}"


@pytest.mark.django_db(transaction=True)
def test_channel_sync_preserves_overlong_display_name_and_content_id(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    """Wide RFC-5322 display names and Content-IDs within field limits stay lossless."""

    del composed_tables
    long_display_name = "Ada " + ("Lovelace " * 80).strip()
    long_cid = f"inline-{'x' * 700}@example.com"
    assert len(long_display_name) > 256
    assert len(long_cid) > 256
    assert Handle._meta.get_field("display_name").max_length >= 4096
    assert Part._meta.get_field("cid").max_length >= 4096

    imap_server.folders.update(
        {
            "INBOX": mailbox(
                (
                    f'From: "{long_display_name}" <ada@example.com>\r\n'
                    "To: bob@example.com\r\n"
                    "Subject: Wide headers\r\n"
                    "Message-ID: <wide-headers@x>\r\n"
                    "Date: Thu, 02 Jul 2026 10:00:00 +0000\r\n"
                    "MIME-Version: 1.0\r\n"
                    "Content-Type: text/html; charset=utf-8\r\n"
                    f"Content-ID: <{long_cid}>\r\n"
                    "\r\n"
                    "<p>Inline body.</p>\r\n"
                ).encode("utf-8")
            )
        }
    )
    channel = _imap_channel(batch_size=1)

    with system_context(reason="test imap wide headers"):
        landed = channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert landed == 1
    handle = Handle._base_manager.get(value="ada@example.com")
    part = Part._base_manager.get(message__external_id="wide-headers@x", type="text/html")
    assert handle.display_name == long_display_name
    assert part.cid == long_cid


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("excess", [0, 1])
def test_channel_sync_bounds_display_names_and_discards_oversized_content_id(
    imap_server: ImapServer,
    composed_tables: None,
    excess: int,
) -> None:
    """Column-sized headers stay intact; larger display text and CIDs land safely."""

    del composed_tables
    name_limit = Handle._meta.get_field("display_name").max_length
    cid_limit = Part._meta.get_field("cid").max_length
    name = "é" * (name_limit + excess)
    cid = "x" * (cid_limit + excess)
    raw = _eml(
        message_id="<bounded-headers@x>",
        sender=f'"{name}" <ada@example.com>',
        to=f'"{name}" <bob@example.com>',
        extra_headers=f'Cc: "{name}" <carol@example.com>\r\nContent-ID: <{cid}>',
        body="<p>Retained body.</p>",
    ).replace(b'Content-Type: text/plain; charset="utf-8"', b'Content-Type: text/html; charset="utf-8"')
    imap_server.folders.update({"INBOX": mailbox(raw)})
    channel = _imap_channel(batch_size=1)

    with system_context(reason="test imap bounded source headers"):
        assert channel.run_sync(now=INTERNAL_DATE) == 1

    assert list(Handle._base_manager.values_list("display_name", flat=True)) == [name[:name_limit]] * 3
    part = Part._base_manager.get(message__external_id="bounded-headers@x", type="text/html")
    assert part.cid == ("" if excess else cid)
    assert part.fragment.text == "<p>Retained body.</p>"
    with system_context(reason="test imap inspect retained failures"):
        assert not any(
            row for stream_row in SyncStream.objects.all() for row in stream_row.sync_failures(open_only=False)
        )


@pytest.mark.django_db(transaction=True)
def test_channel_sync_quarantines_oversized_handle_and_lands_remaining_page(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    """A poison sender leaves one field-specific discrepancy and advances the page."""

    del composed_tables
    value = "x" * Handle._meta.get_field("value").max_length + "@example.com"
    imap_server.folders.update(
        {
            "INBOX": mailbox(
                _eml(message_id="<first@x>", subject="First"),
                _eml(message_id="<poison@x>", subject="Poison", sender=f"Bad <{value}>"),
                _eml(message_id="<last@x>", subject="Last"),
            )
        }
    )
    channel = _imap_channel(batch_size=3)

    with system_context(reason="test imap invalid sender quarantine"):
        assert channel.run_sync(now=INTERNAL_DATE) == 2

    assert set(Message._base_manager.values_list("external_id", flat=True)) == {"first@x", "last@x"}
    assert not Thread._base_manager.filter(title__text="Poison").exists()
    assert not Handle._base_manager.filter(value=value).exists()
    with system_context(reason="test imap inspect retained failures"):
        discrepancy = next(
            row for stream_row in SyncStream.objects.all() for row in stream_row.sync_failures(open_only=False)
        )
    assert discrepancy.code == "invalid_record"
    assert discrepancy.details == {
        "stream": "messages",
        "partition": "INBOX",
        "external_key": "poison@x",
        "fields": ["value"],
        "message": "Record validation failed for fields: value.",
    }
    assert discrepancy.is_open
    assert discrepancy.retry_at is not None
    assert SyncStream.objects.current_for_bridge(channel, "messages").get().cursor == {
        "uidvalidity": 100,
        "last_uid": 3,
    }
    channel.refresh_from_db()
    assert channel.sync_stage == channel.SyncStage.COMPLETED
    assert channel.sync_error == channel.last_error == ""


@pytest.mark.django_db(transaction=True)
def test_channel_sync_lands_threads_parts_and_attachments(
    imap_server: ImapServer,
    composed_tables: None,
    tmp_path: Path,
) -> None:
    """One run drains the mailbox through ingest: threading, roles, files, cursor."""

    del composed_tables
    reply_body = "Yes, confirmed!\n\n> Are we still on for Thursday?\n\n-- \nBob\n"
    long_attachment_name = f"{'x' * 588}.txt"
    imap_server.folders.update(
        {
            "INBOX": mailbox(
                _eml(message_id="<root@x>", subject="Plans", body="Are we still on for Thursday?\n"),
                _eml(
                    message_id="<reply@x>",
                    subject="Re: Plans",
                    sender="Bob <bob@example.com>",
                    to="ada@example.com",
                    extra_headers="In-Reply-To: <root@x>\r\nReferences: <root@x>",
                    body=reply_body,
                ),
                (
                    "From: carol@example.com\r\n"
                    "To: ada@example.com\r\n"
                    "Subject: Contract\r\n"
                    "Message-ID: <files@x>\r\n"
                    "Date: Thu, 02 Jul 2026 11:00:00 +0000\r\n"
                    "MIME-Version: 1.0\r\n"
                    'Content-Type: multipart/mixed; boundary="B1"\r\n'
                    "\r\n"
                    "--B1\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n"
                    "\r\n"
                    "Signed copy attached.\r\n"
                    "--B1\r\n"
                    f'Content-Type: text/plain; name="{long_attachment_name}"\r\n'
                    f'Content-Disposition: attachment; filename="{long_attachment_name}"\r\n'
                    "\r\n"
                    "AGREED TERMS\r\n"
                    "--B1--\r\n"
                ).encode(),
            )
        }
    )
    channel = _imap_channel(batch_size=2, own_addresses=["ada@example.com"])
    with system_context(reason="test imap channel storage"):
        _storage_drive(tmp_path, owner=channel.owner)

    with system_context(reason="test imap channel sync"):
        landed = channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert landed == 3
    assert Message._base_manager.count() == 3

    root = Message._base_manager.get(external_id="root@x")
    reply = Message._base_manager.get(external_id="reply@x")
    files = Message._base_manager.get(external_id="files@x")
    assert reply.thread_id == root.thread_id  # In-Reply-To joined the thread
    assert files.thread_id != root.thread_id
    assert Thread._base_manager.get(pk=root.thread_id).message_count == 2
    assert root.direction == Message.Direction.OUTBOUND  # own address sent it
    assert reply.direction == Message.Direction.INBOUND
    assert reply.metadata["flags"] == ["\\Seen"]
    assert reply.metadata["uid"] == 2

    reply_roles = {
        (part.role, part.fragment.text)
        for part in Part._base_manager.select_related("fragment").filter(message=reply, fragment__isnull=False)
    }
    # The subject rides a sparse TITLE part beside the body roles.
    assert (Part.PartRole.TITLE, "Re: Plans") in reply_roles
    assert (Part.PartRole.QUOTED, "Are we still on for Thursday?") in reply_roles
    assert (Part.PartRole.SIGNATURE, "Bob") in reply_roles
    root_body = Part._base_manager.select_related("fragment").get(
        message=root, role=Part.PartRole.BODY, fragment__isnull=False
    )
    # The stripped quoted paragraph re-used the root body's content-addressed fragment.
    quoted = Part._base_manager.select_related("fragment").get(message=reply, role=Part.PartRole.QUOTED)
    assert quoted.fragment_id == root_body.fragment_id

    attachment = Part._base_manager.select_related("file").get(message=files, file__isnull=False)
    assert len(long_attachment_name) == 592
    assert len(attachment.name) == 512
    assert attachment.name.endswith(".txt")
    assert attachment.disposition == Part.Disposition.ATTACHMENT
    assert attachment.file.filename == attachment.name
    assert attachment.file.size_bytes == len(b"AGREED TERMS")

    assert Participant._base_manager.filter(message=reply).count() == 2  # from + to

    channel.refresh_from_db()
    assert current_stream(channel, "messages", "INBOX").cursor == {"uidvalidity": 100, "last_uid": 3}
    assert channel.sync_stage == Channel.SyncStage.COMPLETED
    assert channel.last_sync_items == 3
    assert channel.sync_progress["message"] == "Applied stream page"
    assert channel.sync_progress["details"]["backend"] == "ImapChannelBackend"
    assert channel.sync_progress["details"]["partition"] == "INBOX"
    assert "batch_size" not in channel.sync_progress["details"]
    assert channel.sync_progress["details"]["landed"] == 3
    assert channel.next_sync_at is not None


@pytest.mark.django_db(transaction=True)
def test_attributed_quote_reuses_the_original_body_fragment(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    """A fully library-segmented reply keeps the content-addressed quote link.

    Attribution header, marker-quoted paragraph, and a salutation signature (no
    ``--`` delimiter): the stripped quoted paragraph must hash to the same
    Fragment row as the root message's body after one channel sync.
    """

    del composed_tables
    reply_body = (
        "Yes, confirmed!\n\nOn Thu, Jul 2, 2026 Ada wrote:\n> Are we still on for Thursday?\n\nBest regards,\nBob\n"
    )
    imap_server.folders.update(
        {
            "INBOX": mailbox(
                _eml(message_id="<root@x>", subject="Plans", body="Are we still on for Thursday?\n"),
                _eml(
                    message_id="<reply@x>",
                    subject="Re: Plans",
                    sender="Bob <bob@example.com>",
                    to="ada@example.com",
                    extra_headers="In-Reply-To: <root@x>\r\nReferences: <root@x>",
                    body=reply_body,
                ),
            )
        }
    )
    channel = _imap_channel(batch_size=2)

    with system_context(reason="test imap attributed quote"):
        landed = channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert landed == 2
    root = Message._base_manager.get(external_id="root@x")
    reply = Message._base_manager.get(external_id="reply@x")
    reply_parts = {
        (part.role, part.fragment.text)
        for part in Part._base_manager.select_related("fragment").filter(message=reply, fragment__isnull=False)
    }
    assert (Part.PartRole.BODY, "Yes, confirmed!") in reply_parts
    assert (Part.PartRole.QUOTED, "On Thu, Jul 2, 2026 Ada wrote:") in reply_parts
    assert (Part.PartRole.QUOTED, "Are we still on for Thursday?") in reply_parts
    assert (Part.PartRole.SIGNATURE, "Best regards,\nBob") in reply_parts
    root_body = Part._base_manager.select_related("fragment").get(
        message=root, role=Part.PartRole.BODY, fragment__isnull=False
    )
    quoted = Part._base_manager.get(
        message=reply, role=Part.PartRole.QUOTED, fragment__text="Are we still on for Thursday?"
    )
    assert quoted.fragment_id == root_body.fragment_id


def test_retained_headers_keep_their_emitted_order_and_every_automation_header() -> None:
    """The emitted order is part of the ingest sync hash; automation detection reads these parts."""

    assert imap_parser._RETAINED_HEADERS == ("list-id", "list-unsubscribe", "reply-to", "auto-submitted", "precedence")
    assert set(AUTOMATED_MAIL_HEADERS) <= set(imap_parser._RETAINED_HEADERS)


@pytest.mark.django_db(transaction=True)
def test_channel_sync_dedups_retained_header_fragments(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    """A List-Id lands as a lowercased HEADER part; the shared value is ONE fragment.

    Retained envelope headers are content-addressed like body text, so a mailing
    list's ``List-Id`` repeated across the whole list dedups to a single fragment
    row referenced by each message's HEADER part.
    """

    del composed_tables
    list_id = "Dev list <dev.example.com>"
    imap_server.folders.update(
        {
            "INBOX": mailbox(
                _eml(message_id="<l1@x>", subject="News 1", extra_headers=f"List-Id: {list_id}"),
                _eml(message_id="<l2@x>", subject="News 2", extra_headers=f"List-Id: {list_id}"),
            )
        }
    )
    channel = _imap_channel()

    with system_context(reason="test imap header fragments"):
        assert channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC)) == 2

    headers = list(
        Part._base_manager.select_related("fragment").filter(role=Part.PartRole.HEADER).order_by("message_id")
    )
    assert [part.name for part in headers] == ["list-id", "list-id"]
    assert headers[0].fragment.text == list_id
    assert headers[0].fragment.kind == "header"
    # Both messages point at the same content-addressed fragment row.
    assert headers[0].fragment_id == headers[1].fragment_id


@pytest.mark.django_db(transaction=True)
def test_channel_resync_is_incremental_and_idempotent(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    """A second run fetches only new UIDs; a UIDVALIDITY reset converges without dupes."""

    del composed_tables
    imap_server.folders.update(
        {
            "INBOX": mailbox(
                _eml(message_id="<a@x>", subject="A", body="Alpha.\n"),
                _eml(message_id="<b@x>", subject="B", body="Beta.\n"),
            )
        }
    )
    channel = _imap_channel()

    with system_context(reason="test imap incremental sync"):
        assert channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC)) == 2

        # New mail arrives; only UID 3 downloads.
        imap_server.folders["INBOX"]["messages"][3] = {"raw": _eml(message_id="<c@x>", subject="C", body="Gamma.\n")}
        imap_server.fetches.clear()
        assert channel.run_sync(now=datetime(2026, 7, 2, 12, 5, tzinfo=UTC)) == 1
        assert all(uids == (3,) for _folder_name, uids, _data in imap_server.fetches)
        assert Message._base_manager.count() == 3

        # The server regenerates the mailbox: same messages, new UID space.
        imap_server.folders["INBOX"]["uidvalidity"] = 999
        imap_server.folders["INBOX"]["messages"] = {
            101: {"raw": _eml(message_id="<a@x>", subject="A", body="Alpha.\n")},
            102: {"raw": _eml(message_id="<b@x>", subject="B", body="Beta.\n")},
            103: {"raw": _eml(message_id="<c@x>", subject="C", body="Gamma.\n")},
        }
        channel.refresh_from_db()
        channel.run_sync(now=datetime(2026, 7, 2, 12, 10, tzinfo=UTC))

    assert Message._base_manager.count() == 3  # refetch converged, no duplicates
    channel.refresh_from_db()
    stream = current_stream(channel, "messages", "INBOX")
    assert stream.cursor == {"uidvalidity": 999, "last_uid": 103}
    assert stream.generation == 2
    assert RecordLink._base_manager.count() == 0


def _attached(message_id: str) -> bytes:
    """Return a message with one attachment; with no storage drive its page cannot land."""

    return (
        f"From: carol@example.com\r\nTo: ada@example.com\r\nSubject: Contract\r\nMessage-ID: {message_id}\r\n"
        "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"B1\"\r\n\r\n"
        "--B1\r\nContent-Type: text/plain\r\n\r\nSigned copy attached.\r\n"
        "--B1\r\nContent-Type: text/plain\r\nContent-Disposition: attachment; filename=\"terms.txt\"\r\n\r\n"
        "AGREED TERMS\r\n--B1--\r\n"
    ).encode()


@pytest.mark.django_db(transaction=True)
def test_failed_run_never_persists_the_cursor(imap_server: ImapServer, composed_tables: None) -> None:
    """A run that dies after fetching keeps the old cursor, so nothing is skipped."""

    del composed_tables
    imap_server.folders.update({"INBOX": mailbox(_attached("<a@x>"))})
    channel = _imap_channel()

    with system_context(reason="test imap failed sync"), pytest.raises(BridgeSyncError) as refused:
        channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert isinstance(refused.value.__cause__, UploadTargetNotFound)
    channel.refresh_from_db()
    assert current_stream(channel, "messages", "INBOX").cursor == {}
    assert Message._base_manager.count() == 0
    assert channel.sync_stage == Channel.SyncStage.FAILED
    assert channel.sync_error == channel.last_error == "messages (INBOX): Integration operation failed."
    assert channel.sync_progress["details"] == {
        "stream": "messages",
        "partition": "INBOX",
        "stage": "pull",
        "failures": [
            {
                "stream": "messages",
                "partition": "INBOX",
                "stage": "pull",
                "message": "Integration operation failed.",
            }
        ],
    }


@pytest.mark.django_db(transaction=True)
def test_failed_run_keeps_successfully_ingested_batch_cursor(imap_server: ImapServer, composed_tables: None) -> None:
    """A later batch failure resumes after already-landed messages."""

    del composed_tables
    imap_server.folders["INBOX"] = mailbox(_eml(message_id="<a@x>"), _eml(message_id="<b@x>"), _attached("<c@x>"))
    channel = _imap_channel(batch_size=2)

    with system_context(reason="test imap partial sync"), pytest.raises(BridgeSyncError) as refused:
        channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert isinstance(refused.value.__cause__, UploadTargetNotFound)
    assert Message._base_manager.count() == 2
    channel.refresh_from_db()
    assert current_stream(channel, "messages", "INBOX").cursor == {"uidvalidity": 100, "last_uid": 2}
    assert channel.sync_stage == channel.SyncStage.FAILED
    assert channel.sync_error == channel.last_error == "messages (INBOX): Integration operation failed."


@pytest.mark.django_db(transaction=True)
def test_failed_second_record_rolls_back_the_whole_page(imap_server: ImapServer, composed_tables: None) -> None:
    """A later infrastructure failure in one page rolls back its messages and cursor."""

    del composed_tables
    imap_server.folders.update({"INBOX": mailbox(_eml(message_id="<a@x>"), _attached("<b@x>"))})
    channel = _imap_channel(batch_size=2)

    with system_context(reason="test imap page rollback"), pytest.raises(BridgeSyncError) as refused:
        channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))
    assert isinstance(refused.value.__cause__, UploadTargetNotFound)
    assert Message._base_manager.count() == 0
    assert current_stream(channel, "messages", "INBOX").cursor == {}


@pytest.mark.django_db(transaction=True)
def test_page_closure_resolves_quotes_of_a_later_record(
    imap_server: ImapServer,
    composed_tables: None,
) -> None:
    """The original and its later quoted-only sharer link after both page rows land."""

    del composed_tables
    paragraph = "Please retain this complete paragraph for our meeting tomorrow."
    imap_server.folders.update(
        {
            "INBOX": mailbox(
                _eml(message_id="<original@x>", subject="Meeting", body=paragraph + "\n"),
                _eml(message_id="<quoted@x>", subject="Re: Meeting", body="Agreed.\n\n> " + paragraph + "\n"),
            )
        }
    )
    channel = _imap_channel(batch_size=2)
    with system_context(reason="test imap page quotation closure"):
        assert channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC)) == 2
    original = Message._base_manager.get(external_id="original@x")
    quoted = Message._base_manager.get(external_id="quoted@x")
    assert Part._base_manager.filter(message=quoted, role=Part.PartRole.QUOTED).exists()
    assert MessageEdge._base_manager.filter(src=original, dst=quoted, kind="quote").exists()
