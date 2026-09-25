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
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from django.core.exceptions import ValidationError
from imapclient.exceptions import LoginError
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.integrate.streams import CursorInvalid, StreamDefinition, advance_stream, open_stream
from angee.messaging_integrate_imap import parser as imap_parser
from angee.messaging_integrate_imap.backend import (
    MAX_SAMPLE_MESSAGES,
    ImapChannelBackend,
    ImapError,
    ImapSamplePreviewRequest,
)
from angee.messaging_integrate_imap.parser import (
    fallback_message,
    html_to_text,
    parse_message,
    split_plain_text,
    synthetic_external_id,
)
from angee.testing.models import RecordLink, SyncStream
from tests.conftest import make_integration
from tests.stream_adapters import AdapterPages
from tests.test_messaging import (
    Handle,
    Message,
    MessageEdge,
    Part,
    Participant,
    Thread,
    _storage_drive,
)
from tests.test_messaging_graphql import Channel

_INTERNAL_DATE = datetime(2026, 7, 2, 9, 30, tzinfo=UTC)


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
        "internal_date": _INTERNAL_DATE,
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
    assert parsed.received_at == _INTERNAL_DATE
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
    assert _parse(raw).sent_at == _INTERNAL_DATE


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


def test_attachment_filename_is_clamped_to_storage_limit() -> None:
    """An absurd MIME filename should not abort attachment ingestion downstream."""

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
    assert len(attachment.name) == 512
    assert attachment.name.endswith(".pdf")
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


def test_embedded_rfc822_expansion_limit_retains_raw_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded-out attached message remains raw evidence for explicit review."""

    inner = _eml(message_id="<inner-limit@example.com>", subject="Bounded report")
    raw = (
        b"From: ada@example.com\r\nTo: bob@example.com\r\nSubject: Fwd\r\n"
        b"Message-ID: <fwd-limit@example.com>\r\nMIME-Version: 1.0\r\n"
        b"Content-Type: message/rfc822\r\n\r\n" + inner
    )
    budget_type = imap_parser._EmbeddedMessageBudget
    monkeypatch.setattr(
        imap_parser,
        "_EmbeddedMessageBudget",
        lambda: budget_type(remaining_parts=256, remaining_bytes=1),
    )
    embedded = _parse(raw).body
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
        internal_date=_INTERNAL_DATE,
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


class FakeImapAccount:
    """In-memory IMAP account state shared by the fake client's connections."""

    def __init__(self, folders: dict[str, dict[str, Any]]) -> None:
        self.folders = folders
        self.logins: list[tuple[str, str, str]] = []
        self.status_calls: list[str] = []
        self.selects: list[str] = []
        self.searches: list[tuple[str, Any]] = []
        self.fetches: list[tuple[str, tuple[int, ...], tuple[bytes, ...]]] = []
        self.logouts = 0

    def uids(self, folder: str) -> list[int]:
        return sorted(self.folders[folder].get("messages", {}))


class FakeIMAPClient:
    """The IMAPClient surface the backend drives, over a FakeImapAccount."""

    account: ClassVar[FakeImapAccount]

    def __init__(
        self,
        host: str,
        *,
        port: int | None = None,
        ssl: bool = True,
        ssl_context: Any = None,
        timeout: int | None = None,
    ) -> None:
        del host, port, ssl, ssl_context, timeout
        self._selected = ""
        # The real IMAPClient defaults to converting INTERNALDATE to naive local
        # time; the backend must opt out, and this fake's aware datetimes are
        # only faithful once it has.
        self.normalise_times = True

    def login(self, username: str, password: str) -> None:
        self.account.logins.append(("login", username, password))

    def oauth2_login(self, user: str, access_token: str) -> None:
        self.account.logins.append(("oauth2", user, access_token))

    def starttls(self, ssl_context: Any = None) -> None:
        del ssl_context

    def list_folders(self) -> list[tuple[tuple[bytes, ...], bytes, str]]:
        return [
            (folder.get("flags", (b"\\HasNoChildren",)), b"/", name) for name, folder in self.account.folders.items()
        ]

    def folder_status(self, name: str, what: Any = None) -> dict[bytes, int]:
        del what
        self.account.status_calls.append(name)
        return {
            b"UIDVALIDITY": self.account.folders[name]["uidvalidity"],
            b"UIDNEXT": max(self.account.uids(name), default=0) + 1,
        }

    def select_folder(self, name: str, readonly: bool = False) -> dict[bytes, int]:
        assert readonly, "the sync must never open a folder read-write"
        self._selected = name
        self.account.selects.append(name)
        return {b"UIDVALIDITY": self.account.folders[name]["uidvalidity"]}

    def search(self, criteria: Any = "ALL") -> list[int]:
        self.account.searches.append((self._selected, criteria))
        uids = self.account.uids(self._selected)
        if isinstance(criteria, (list, tuple)) and "UID" in criteria:
            sequence = str(criteria[criteria.index("UID") + 1])
            if "," in sequence:
                requested = {int(value) for value in sequence.split(",")}
                uids = [uid for uid in uids if uid in requested]
            elif ":" not in sequence:
                requested = int(sequence)
                uids = [uid for uid in uids if uid == requested]
            else:
                start, end = sequence.split(":", 1)
                lower = int(start)
                upper = max(uids, default=0) if end == "*" else int(end)
                lower, upper = sorted((lower, upper))
                uids = [uid for uid in uids if lower <= uid <= upper]
        if isinstance(criteria, (list, tuple)):
            messages = self.account.folders[self._selected]["messages"]
            if "SINCE" in criteria:
                since = criteria[criteria.index("SINCE") + 1]
                uids = [uid for uid in uids if messages[uid].get("internal_date", _INTERNAL_DATE).date() >= since]
            if "BEFORE" in criteria:
                before = criteria[criteria.index("BEFORE") + 1]
                uids = [uid for uid in uids if messages[uid].get("internal_date", _INTERNAL_DATE).date() < before]
        return uids

    def fetch(self, uids: list[int], data: list[bytes]) -> dict[int, dict[bytes, Any]]:
        assert self.normalise_times is False, "the sync must disable naive-local INTERNALDATE normalisation"
        self.account.fetches.append((self._selected, tuple(uids), tuple(data)))
        messages = self.account.folders[self._selected].get("messages", {})
        response: dict[int, dict[bytes, Any]] = {}
        for uid in uids:
            entry = messages.get(uid)
            if entry is None:
                continue
            raw = entry["raw"]
            item: dict[bytes, Any] = {}
            for key in data:
                if key == b"RFC822.SIZE":
                    item[key] = len(raw)
                elif key == b"FLAGS":
                    item[key] = entry.get("flags", (b"\\Seen",))
                elif key == b"INTERNALDATE":
                    item[key] = entry.get("internal_date", _INTERNAL_DATE)
                elif key == b"BODY.PEEK[]":
                    item[b"BODY[]"] = raw
                elif key == b"BODY.PEEK[HEADER]":
                    item[b"BODY[HEADER]"] = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            response[uid] = item
        return response

    def logout(self) -> None:
        self.account.logouts += 1


class _BridgeStub:
    """Just enough of a Channel row for the backend's transport/cursor logic."""

    def __init__(self, *, config: dict[str, Any] | None = None, credential: Any = None) -> None:
        self.config = {"host": "192.0.2.10", **(config or {})}
        self.cursor: dict[str, Any] = {}
        self.credential = credential
        self._state = SimpleNamespace(adding=False, db="default")

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


def _backend(
    monkeypatch: pytest.MonkeyPatch,
    account: FakeImapAccount,
    *,
    config: dict[str, Any] | None = None,
    credential: Any = None,
) -> ImapChannelBackend:
    """Bind an ImapChannelBackend to a stub bridge and the fake client."""

    monkeypatch.setattr(FakeIMAPClient, "account", account, raising=False)
    monkeypatch.setattr(ImapChannelBackend, "client_class", FakeIMAPClient)
    bridge = _BridgeStub(config=config, credential=credential or _BasicCredentialStub())
    backend = ImapChannelBackend(bridge)
    backend.test_pages = AdapterPages(backend)
    return backend


def _folder(*raws: bytes, uidvalidity: int = 100, flags: tuple[bytes, ...] = (b"\\HasNoChildren",)) -> dict[str, Any]:
    """Build a fake folder whose messages get ascending UIDs from one."""

    return {
        "flags": flags,
        "uidvalidity": uidvalidity,
        "messages": {index + 1: {"raw": raw} for index, raw in enumerate(raws)},
    }


def _drain(backend: ImapChannelBackend) -> list[Any]:
    """Drain the backend the way Channel.sync does, collecting every message."""

    collected: list[Any] = []
    while batch := backend.test_pages.next_batch():
        collected.extend(batch)
    return collected


def test_web_sample_selection_limit_matches_backend() -> None:
    """The preview's selection cap must stay within the import owner's bound."""

    action = Path(__file__).parents[1] / "addons/angee/messaging_integrate_imap/web/src/ImportImapSampleAction.tsx"
    assert f"const IMAP_SAMPLE_LIMIT = {MAX_SAMPLE_MESSAGES};" in action.read_text()


def test_sample_preview_is_bounded_readonly_and_keeps_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    account = FakeImapAccount({"INBOX": _folder(_eml(subject="A"), _eml(subject="B"), _eml(subject="C"))})
    backend = _backend(monkeypatch, account)
    backend.bridge.cursor = {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 1}}}

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
    assert account.searches == [("INBOX", ["UID", "1:3", "SINCE", date(2026, 7, 1), "BEFORE", date(2026, 8, 1)])]
    assert all(b"BODY.PEEK[]" not in fields for _, _, fields in account.fetches)
    assert backend.bridge.cursor == {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 1}}}
    assert len(account.logins) == account.logouts == 1


def test_sample_preview_pages_one_all_dates_snapshot_and_excludes_new_mail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = FakeImapAccount({"INBOX": _folder(*(_eml(subject=str(index)) for index in range(1, 6)))})
    backend = _backend(monkeypatch, account)

    first = backend.preview_sample(
        ImapSamplePreviewRequest(
            mailbox="INBOX",
            since=None,
            before=None,
            all_dates=True,
            limit=2,
        )
    )
    account.folders["INBOX"]["messages"][6] = {"raw": _eml(subject="new")}
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
    assert account.searches == [("INBOX", ["UID", f"1:{upper}"]) for upper in (5, 3, 1)]
    assert backend.bridge.cursor == {}


def test_sample_preview_rejects_still_present_unanswered_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = FakeImapAccount({"INBOX": _folder(_eml(subject="A"), _eml(subject="B"))})
    backend = _backend(monkeypatch, account)
    original_fetch = FakeIMAPClient.fetch

    def omit_second_header(self: FakeIMAPClient, uids: list[int], data: list[bytes]) -> dict[int, dict[bytes, Any]]:
        response = original_fetch(self, uids, data)
        if b"BODY.PEEK[HEADER]" in data:
            response.pop(2, None)
        return response

    monkeypatch.setattr(FakeIMAPClient, "fetch", omit_second_header)

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

    assert ("INBOX", ["UID", "2"]) in account.searches
    assert backend.bridge.cursor == {}


@pytest.mark.parametrize("expunged", [(3,), (3, 2)])
def test_sample_preview_confirmed_expunges_reduce_retained_count_and_advance(
    monkeypatch: pytest.MonkeyPatch,
    expunged: tuple[int, ...],
) -> None:
    account = FakeImapAccount({"INBOX": _folder(*(_eml(subject=str(uid)) for uid in range(1, 6)))})
    backend = _backend(monkeypatch, account)
    request = ImapSamplePreviewRequest(mailbox="INBOX", all_dates=True, limit=2)
    first = backend.preview_sample(request)
    original_fetch = FakeIMAPClient.fetch

    def expunge_before_headers(
        self: FakeIMAPClient,
        uids: list[int],
        data: list[bytes],
    ) -> dict[int, dict[bytes, Any]]:
        if b"BODY.PEEK[HEADER]" in data:
            for uid in set(uids) & set(expunged):
                self.account.folders["INBOX"]["messages"].pop(uid)
        return original_fetch(self, uids, data)

    monkeypatch.setattr(FakeIMAPClient, "fetch", expunge_before_headers)
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
    assert backend.bridge.cursor == {}


def test_sample_preview_date_window_continuation_preserves_server_search_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = _folder(*(_eml(subject=str(uid)) for uid in range(1, 7)))
    folder["messages"][1]["internal_date"] = datetime(2026, 6, 30, tzinfo=UTC)
    folder["messages"][6]["internal_date"] = datetime(2026, 8, 1, tzinfo=UTC)
    account = FakeImapAccount({"INBOX": folder})
    backend = _backend(monkeypatch, account)
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
    assert account.searches == [
        ("INBOX", ["UID", f"1:{upper}", "SINCE", request.since, "BEFORE", request.before]) for upper in (6, 3)
    ]


@pytest.mark.parametrize("changed_epoch", [False, True])
def test_sample_preview_rejects_unavailable_snapshot_or_changed_continuation_epoch(
    monkeypatch: pytest.MonkeyPatch,
    changed_epoch: bool,
) -> None:
    account = FakeImapAccount({"INBOX": _folder(_eml(), _eml())})
    backend = _backend(monkeypatch, account)
    request = ImapSamplePreviewRequest(mailbox="INBOX", all_dates=True, limit=1)
    first = backend.preview_sample(request)
    if changed_epoch:
        account.folders["INBOX"]["uidvalidity"] += 1
    else:
        account.folders["INBOX"]["messages"].pop(2)
    searched = len(account.searches)

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

    assert len(account.searches) == searched
    assert backend.bridge.cursor == {}
    assert account.logouts == 2


@pytest.mark.parametrize("operation", ["preview", "prepare", "sync"])
def test_missing_uidnext_is_an_actionable_imap_error(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    account = FakeImapAccount({"INBOX": _folder(_eml())})
    backend = _backend(monkeypatch, account)
    original_status = FakeIMAPClient.folder_status

    def missing_uidnext(self: FakeIMAPClient, name: str, what: Any = None) -> dict[bytes, int]:
        status = original_status(self, name, what)
        del status[b"UIDNEXT"]
        return status

    monkeypatch.setattr(FakeIMAPClient, "folder_status", missing_uidnext)
    with pytest.raises(ImapError, match="did not report a valid UIDNEXT.*Check the server's IMAP STATUS support"):
        if operation == "preview":
            backend.preview_sample(ImapSamplePreviewRequest(mailbox="INBOX", all_dates=True))
        elif operation == "prepare":
            backend.prepare_new_mail_boundary()
        else:
            backend.test_pages.next_batch()
    assert backend.bridge.cursor.get("mailboxes", {}) == {}
    assert all(cursor == {} for cursor in backend.test_pages.cursors.values())
    backend.close()


def test_sample_fetch_pins_uids_and_preserves_unread_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _folder(_eml(message_id="a@sample", subject="A"), _eml(message_id="b@sample", subject="B"))
    folder["messages"][2]["flags"] = ()
    account = FakeImapAccount({"INBOX": folder})
    backend = _backend(monkeypatch, account)

    messages, imported, unchanged = backend.fetch_sample(mailbox="INBOX", uidvalidity=100, uids=[2, 99])

    assert [message.subject for message in messages] == ["B"]
    assert imported == [2] and unchanged
    assert folder["messages"][2]["flags"] == ()
    assert backend.bridge.cursor == {}
    assert account.searches == []
    assert all(set(uids) <= {2, 99} for _, uids, _ in account.fetches)
    assert len(account.logins) == account.logouts == 1


def test_sample_rejects_changed_mailbox_epoch_before_body_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    account = FakeImapAccount({"INBOX": _folder(_eml(), uidvalidity=200)})
    backend = _backend(monkeypatch, account)
    with pytest.raises(ValidationError, match="UID identity changed"):
        backend.fetch_sample(mailbox="INBOX", uidvalidity=100, uids=[1])
    assert account.fetches == []
    assert backend.bridge.cursor == {}
    assert account.logouts == 1


def test_sample_rejects_unbounded_requests_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    account = FakeImapAccount({"INBOX": _folder(_eml())})
    backend = _backend(monkeypatch, account)
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
    assert account.logins == []


def test_backfill_pages_in_batches_and_sets_the_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Initial sync drains a folder in batch_size pages and records its watermark."""

    account = FakeImapAccount(
        {
            "INBOX": _folder(
                _eml(message_id="<a@x>", subject="A"),
                _eml(message_id="<b@x>", subject="B"),
                _eml(message_id="<c@x>", subject="C"),
            ),
            "Junk": _folder(_eml(message_id="<spam@x>"), flags=(b"\\Junk",)),
        }
    )
    backend = _backend(monkeypatch, account, config={"batch_size": 2})

    first = backend.test_pages.next_batch()
    second = backend.test_pages.next_batch()
    third = backend.test_pages.next_batch()

    assert [message.subject for message in first] == ["A", "B"]
    assert [message.subject for message in second] == ["C"]
    assert third == []
    assert account.selects == ["INBOX"]  # Junk never opened
    assert backend.test_pages.cursors["INBOX"] == {"uidvalidity": 100, "last_uid": 3}
    assert account.logouts == 1
    assert account.logins == [("login", "ada@example.com", "pw")]


def test_present_unanswered_uid_fails_before_cursor_advance(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient FETCH omission remains retryable while the UID still exists."""

    account = FakeImapAccount({"INBOX": _folder(_eml(subject="A"), _eml(subject="B"))})
    backend = _backend(monkeypatch, account)
    original_fetch = FakeIMAPClient.fetch

    def omit_second_body(self: FakeIMAPClient, uids: list[int], data: list[bytes]) -> dict[int, dict[bytes, Any]]:
        response = original_fetch(self, uids, data)
        if b"BODY.PEEK[]" in data:
            response.pop(2, None)
        return response

    monkeypatch.setattr(FakeIMAPClient, "fetch", omit_second_body)

    with pytest.raises(ImapError, match="still contains UID"):
        backend.test_pages.next_batch()

    assert backend.test_pages.cursors == {"INBOX": {}}
    assert ("INBOX", ["UID", "2"]) in account.searches
    assert account.selects == ["INBOX"]


def test_confirmed_expunged_uid_allows_cursor_advance(monkeypatch: pytest.MonkeyPatch) -> None:
    """A UID expunged after discovery does not pin the mailbox watermark forever."""

    account = FakeImapAccount({"INBOX": _folder(_eml(subject="A"), _eml(subject="B"))})
    backend = _backend(monkeypatch, account)
    original_fetch = FakeIMAPClient.fetch

    def expunge_before_fetch(self: FakeIMAPClient, uids: list[int], data: list[bytes]) -> dict[int, dict[bytes, Any]]:
        if b"RFC822.SIZE" in data:
            account.folders["INBOX"]["messages"].pop(2, None)
        return original_fetch(self, uids, data)

    monkeypatch.setattr(FakeIMAPClient, "fetch", expunge_before_fetch)

    messages = backend.test_pages.next_batch()

    assert [message.subject for message in messages] == ["A"]
    assert backend.test_pages.cursors["INBOX"] == {"uidvalidity": 100, "last_uid": 2}
    assert ("INBOX", ["UID", "2"]) in account.searches


@pytest.mark.parametrize("operation", ["sync", "preview", "sample"])
def test_unanswered_uid_confirmation_rejects_changed_epoch(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Existence confirmation cannot cross into a regenerated UID namespace."""

    account = FakeImapAccount({"INBOX": _folder(_eml(subject="A"), _eml(subject="B"))})
    backend = _backend(monkeypatch, account)
    original_fetch = FakeIMAPClient.fetch

    def omit_and_change_epoch(self: FakeIMAPClient, uids: list[int], data: list[bytes]) -> dict[int, dict[bytes, Any]]:
        response = original_fetch(self, uids, data)
        if b"BODY.PEEK[]" in data or b"BODY.PEEK[HEADER]" in data:
            response.pop(2, None)
            account.folders["INBOX"]["uidvalidity"] = 200
        return response

    monkeypatch.setattr(FakeIMAPClient, "fetch", omit_and_change_epoch)

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
        assert account.logouts == 1

    assert backend.bridge.cursor == {}
    assert account.selects == ["INBOX"]


def test_gmail_all_mail_special_use_wins_folder_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``\\All`` folder already holds every non-junk message, so it syncs alone."""

    account = FakeImapAccount(
        {
            "INBOX": _folder(_eml(message_id="<i@x>")),
            "[Gmail]/All Mail": _folder(_eml(message_id="<i@x>"), _eml(message_id="<s@x>"), flags=(b"\\All",)),
            "[Gmail]/Trash": _folder(flags=(b"\\Trash",)),
        }
    )
    backend = _backend(monkeypatch, account)

    messages = _drain(backend)

    assert {message.metadata["mailbox"] for message in messages} == {"[Gmail]/All Mail"}
    assert account.selects == ["[Gmail]/All Mail"]


def test_each_mailbox_fetches_from_its_own_selected_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """FETCH is stateful: every chunk pins its mailbox, so multi-folder mail never crosses.

    Discovery leaves the last-planned folder selected; without the per-chunk
    re-select, the first folder's UIDs would fetch from the wrong mailbox —
    mislabelling one folder's mail and silently skipping the other's while its
    cursor still advanced.
    """

    account = FakeImapAccount(
        {
            "INBOX": _folder(
                _eml(message_id="<i1@x>", subject="I1"),
                _eml(message_id="<i2@x>", subject="I2"),
            ),
            "Archive": _folder(_eml(message_id="<a1@x>", subject="A1")),
        }
    )
    backend = _backend(monkeypatch, account, config={"mailboxes": ["INBOX", "Archive"]})

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


def test_operator_skip_outranks_the_all_mail_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured skip of the ``\\All`` folder falls back to normal selection."""

    account = FakeImapAccount(
        {
            "INBOX": _folder(_eml(message_id="<i@x>")),
            "[Gmail]/All Mail": _folder(_eml(message_id="<i@x>"), flags=(b"\\All",)),
        }
    )
    backend = _backend(monkeypatch, account, config={"skip_mailboxes": ["[Gmail]/All Mail"]})

    messages = _drain(backend)

    assert {message.metadata["mailbox"] for message in messages} == {"INBOX"}


def test_explicit_mailboxes_config_wins_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator's mailboxes list overrides special-use and skip heuristics."""

    account = FakeImapAccount(
        {
            "INBOX": _folder(_eml(message_id="<i@x>")),
            "Archive/2026": _folder(_eml(message_id="<old@x>")),
        }
    )
    backend = _backend(monkeypatch, account, config={"mailboxes": ["Archive/2026"]})

    messages = _drain(backend)

    assert [message.metadata["mailbox"] for message in messages] == ["Archive/2026"]


def test_incremental_run_prescreens_with_uidnext(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unchanged folder costs one STATUS — no SELECT, no SEARCH, no FETCH."""

    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<a@x>"), _eml(message_id="<b@x>"))})
    backend = _backend(monkeypatch, account)
    backend.bridge.cursor = {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 2}}}

    assert backend.test_pages.next_batch() == []
    assert account.status_calls == ["INBOX"]
    assert account.selects == []
    assert account.fetches == []


def test_incremental_run_fetches_only_new_uids(monkeypatch: pytest.MonkeyPatch) -> None:
    """New mail beyond the watermark is fetched; old UIDs never re-download."""

    account = FakeImapAccount(
        {
            "INBOX": _folder(
                _eml(message_id="<a@x>", subject="A"),
                _eml(message_id="<b@x>", subject="B"),
                _eml(message_id="<c@x>", subject="C"),
            )
        }
    )
    backend = _backend(monkeypatch, account)
    backend.bridge.cursor = {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 2}}}

    messages = _drain(backend)

    assert [message.subject for message in messages] == ["C"]
    assert account.searches == [("INBOX", ["UID", "3:*"])]
    assert backend.test_pages.cursors["INBOX"]["last_uid"] == 3


def test_uid_star_range_quirk_is_filtered_client_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server echoing the max UID below the watermark yields no refetch."""

    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<a@x>"), _eml(message_id="<b@x>"))})
    backend = _backend(monkeypatch, account)
    backend.bridge.cursor = {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 2}}}
    # A UID was allocated then expunged server-side: UIDNEXT moved past the
    # watermark, so the prescreen lets the search through, and the search echoes
    # only the highest existing UID (2) per the RFC 3501 ``n:*`` quirk.
    monkeypatch.setattr(
        FakeIMAPClient,
        "folder_status",
        lambda self, name, what=None: {b"UIDVALIDITY": 100, b"UIDNEXT": 4},
    )

    assert backend.test_pages.next_batch() == []
    assert account.fetches == []  # the echoed max-UID (2) was filtered, nothing fetched


def test_uidvalidity_change_resets_the_folder_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A regenerated mailbox refetches from scratch under its new UID space."""

    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<a@x>", subject="A"), uidvalidity=777)})
    backend = _backend(monkeypatch, account)
    backend.bridge.cursor = {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 50}}}

    with pytest.raises(CursorInvalid) as invalid:
        _drain(backend)
    stream = backend.test_pages.rows["INBOX"]
    stream.generation += 1
    stream.cursor = invalid.value.cursor
    messages = _drain(backend)

    assert [message.subject for message in messages] == ["A"]
    assert account.searches == [("INBOX", "ALL")]
    assert backend.test_pages.cursors["INBOX"] == {"uidvalidity": 777, "last_uid": 1}


def test_oversized_message_lands_header_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A message over the size cap keeps its envelope with a truncation marker."""

    big = _eml(message_id="<big@x>", subject="Big", body="x" * 2000)
    small = _eml(message_id="<small@x>", subject="Small")
    account = FakeImapAccount({"INBOX": _folder(small, big)})
    backend = _backend(monkeypatch, account, config={"max_message_bytes": 1000})

    messages = {message.subject: message for message in _drain(backend)}

    assert messages["Small"].metadata.get("truncated_bytes") is None
    assert messages["Big"].metadata["truncated_bytes"] == len(big)
    assert messages["Big"].body is None
    assert messages["Big"].external_id == "big@x"


def test_poison_message_lands_through_the_fallback_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """A message the MIME layer rejects still lands with its raw bytes attached."""

    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<p@x>", subject="Poison"))})
    backend = _backend(monkeypatch, account)

    def explode(raw: bytes, **kwargs: Any) -> Any:
        raise ValueError("unparseable")

    monkeypatch.setattr("angee.messaging_integrate_imap.backend.parse_message", explode)
    messages = _drain(backend)

    assert len(messages) == 1
    assert messages[0].metadata["parse_error"] == "ValueError: unparseable"
    assert messages[0].body.content.startswith(b"From:")


def test_oauth_credential_refreshes_then_authenticates_xoauth2(monkeypatch: pytest.MonkeyPatch) -> None:
    """An OAuth channel freshens its token and logs in over XOAUTH2."""

    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<a@x>"))})
    credential = _OAuthCredentialStub()
    backend = _backend(
        monkeypatch,
        account,
        config={"username": "ada@example.com"},
        credential=credential,
    )

    _drain(backend)

    assert credential.freshened == 1
    assert account.logins == [("oauth2", "ada@example.com", "token-123")]


def test_unusable_configuration_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing host, unknown security mode, and unsupported credentials all raise."""

    account = FakeImapAccount({"INBOX": _folder()})

    backend = _backend(monkeypatch, account)
    backend.bridge.config["host"] = ""
    with pytest.raises(ImapError, match="host"):
        backend.test_pages.next_batch()

    backend = _backend(monkeypatch, account, config={"security": "carrier-pigeon"})
    with pytest.raises(ImapError, match="security"):
        backend.test_pages.next_batch()

    class _SshCredential:
        kind = CredentialKind.SSH_KEY
        external_account = None

    backend = _backend(monkeypatch, account, credential=_SshCredential())
    with pytest.raises(ImapError, match="credential"):
        backend.test_pages.next_batch()

    backend = _backend(monkeypatch, account)
    backend.bridge.credential = None
    with pytest.raises(ImapError, match="credential"):
        backend.test_pages.next_batch()


def test_host_is_judged_by_the_outbound_address_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private self-hosted mail is allowed; metadata/link-local escapes never are."""

    account = FakeImapAccount({"INBOX": _folder()})
    backend = _backend(monkeypatch, account, config={"host": "169.254.169.254"})
    with pytest.raises(ImapError, match="forbidden"):
        backend.test_pages.next_batch()

    # A private (RFC 1918) host is the legitimate self-hosted case and connects.
    backend = _backend(monkeypatch, account, config={"host": "10.0.0.4"})
    assert backend.test_pages.next_batch() == []
    assert account.logins  # the private host authenticated


def test_login_refusal_names_the_account_and_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected password surfaces as an operator-safe ImapError, never a raw LoginError."""

    account = FakeImapAccount({"INBOX": _folder()})

    def refuse(self: FakeIMAPClient, username: str, password: str) -> None:
        del self, password
        raise LoginError("[AUTHENTICATIONFAILED] Authentication failed. Private server detail: pw")

    monkeypatch.setattr(FakeIMAPClient, "login", refuse)
    backend = _backend(monkeypatch, account, config={"host": "10.0.0.4"})

    with pytest.raises(ImapError) as raised:
        backend.test_pages.next_batch()

    assert raised.value.public_message == (
        "IMAP login failed for 'ada@example.com' at 10.0.0.4. Check the account credentials."
    )
    assert "pw" not in raised.value.public_message
    assert "Private server detail" not in raised.value.public_message
    assert isinstance(raised.value.__cause__, LoginError)


def test_transport_failure_while_dialing_is_an_imap_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused socket at dial time reports the host, not a bare OSError."""

    account = FakeImapAccount({"INBOX": _folder()})

    def refuse_dial(self: FakeIMAPClient, host: str, **kwargs: Any) -> None:
        del self, host, kwargs
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(FakeIMAPClient, "__init__", refuse_dial)
    backend = _backend(monkeypatch, account, config={"host": "10.0.0.4"})

    with pytest.raises(ImapError) as raised:
        backend.test_pages.next_batch()
    assert raised.value.public_message == (
        "IMAP connection to 10.0.0.4 failed. Check the host, port, and security settings."
    )
    assert isinstance(raised.value.__cause__, ConnectionRefusedError)


def test_connection_test_signs_in_and_logs_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connection test is one login round-trip with no mailbox touched."""

    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<a@x>"))})
    backend = _backend(monkeypatch, account, config={"host": "10.0.0.4"})

    message = backend.test_connection()

    assert message == "Signed in to 10.0.0.4 as ada@example.com."
    assert account.logins == [("login", "ada@example.com", "pw")]
    assert account.logouts == 1
    assert account.selects == []
    assert account.fetches == []


def test_transient_transport_error_reconnects_and_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One dropped connection mid-fetch reconnects and completes the batch."""

    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<a@x>", subject="A"))})
    failures = {"remaining": 1}
    original_fetch = FakeIMAPClient.fetch

    def flaky_fetch(self: FakeIMAPClient, uids: list[int], data: list[bytes]) -> dict[int, dict[bytes, Any]]:
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise ConnectionResetError("gone")
        return original_fetch(self, uids, data)

    monkeypatch.setattr(FakeIMAPClient, "fetch", flaky_fetch)
    backend = _backend(monkeypatch, account)

    messages = _drain(backend)

    assert [message.subject for message in messages] == ["A"]
    assert len(account.logins) == 2  # the reconnect re-authenticated


def test_ssl_context_is_the_stdlib_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """TLS trust rides the runtime's default context, never a hand-rolled one."""

    captured: dict[str, Any] = {}
    account = FakeImapAccount({"INBOX": _folder()})
    original_init = FakeIMAPClient.__init__

    def capturing_init(self: FakeIMAPClient, host: str, **kwargs: Any) -> None:
        captured.update(kwargs)
        original_init(self, host, **kwargs)

    monkeypatch.setattr(FakeIMAPClient, "__init__", capturing_init)
    backend = _backend(monkeypatch, account)
    backend.test_pages.next_batch()

    assert isinstance(captured["ssl_context"], ssl.SSLContext)
    assert captured["ssl"] is True


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


@pytest.mark.django_db(transaction=True)
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


def _wire_fake(monkeypatch: pytest.MonkeyPatch, account: FakeImapAccount) -> None:
    """Point the imap backend at the in-memory account."""

    monkeypatch.setattr(FakeIMAPClient, "account", account, raising=False)
    monkeypatch.setattr(ImapChannelBackend, "client_class", FakeIMAPClient)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("existing_stream", [False, True])
def test_legacy_imap_position_seeds_only_an_empty_stream(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch, existing_stream: bool
) -> None:
    """Retained mailbox UIDs continue at cutover and never replace a stream cursor."""

    account = FakeImapAccount(
        {"INBOX": _folder(*(_eml(message_id=f"<seed-{uid}@example.com>", subject=str(uid)) for uid in range(1, 4)))}
    )
    _wire_fake(monkeypatch, account)
    channel = _imap_channel()
    seeds: list[str] = []
    seed_cursor = ImapChannelBackend.seed_cursor

    def track_seed(self: Any, stream: Any, legacy_cursor: Any) -> Any:
        seeds.append(stream.partition)
        return seed_cursor(self, stream, legacy_cursor)

    monkeypatch.setattr(ImapChannelBackend, "seed_cursor", track_seed)
    with system_context(reason="test imap legacy stream position"):
        channel.cursor = {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 1}}}
        channel.save(update_fields=["cursor"])
        if existing_stream:
            SyncStream.objects.current(channel, "messages", "INBOX", cursor={"uidvalidity": 100, "last_uid": 2})
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == (1 if existing_stream else 2)
        assert seeds == ([] if existing_stream else ["INBOX"])
        channel.cursor = {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 0}}}
        channel.save(update_fields=["cursor"])
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 0
        assert seeds == ([] if existing_stream else ["INBOX"])
    assert set(Message._base_manager.values_list("external_id", flat=True)) == (
        {"seed-3@example.com"} if existing_stream else {"seed-2@example.com", "seed-3@example.com"}
    )


@pytest.mark.django_db(transaction=True)
def test_legacy_future_only_policy_survives_a_stream_reset(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<legacy-old@example.com>"))})
    _wire_fake(monkeypatch, account)
    channel = _imap_channel()
    with system_context(reason="test imap legacy future-only policy"):
        backend = channel.backend
        try:
            backend.streams()
            identity = backend._source_identity_digest()
        finally:
            backend.close()
        channel.cursor = {
            "delivery_mode": "new_only",
            "source_identity": identity,
            "mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 1}},
        }
        channel.save(update_fields=["cursor"])
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 0
        stream = SyncStream.objects.current(channel, "messages", "INBOX")
        channel.refresh_from_db()
        assert channel.config["delivery_mode"] == "new_only"
        assert channel.config["source_identity"] == identity
        assert channel.config["mailbox_selection"] == ["INBOX"]
        assert channel.cursor == {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 1}}}
        assert stream.config == {}
        account.folders["INBOX"]["uidvalidity"] = 200
        account.folders["INBOX"]["messages"][2] = {"raw": _eml(message_id="<old-epoch@example.com>")}
        stream.resync_required = True
        stream.save(update_fields=["resync_required"])
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 0
        successor = SyncStream.objects.current(channel, "messages", "INBOX")
        assert successor.generation == stream.generation + 1
        assert successor.config == stream.config
        assert successor.cursor == {"uidvalidity": 200, "last_uid": 2}
    assert not Message._base_manager.exists()


@pytest.mark.django_db(transaction=True)
def test_new_mailbox_cannot_restore_removed_legacy_policy(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<retained-inbox@example.com>"))})
    _wire_fake(monkeypatch, account)
    channel = _imap_channel()
    positions = {name: {"uidvalidity": 100, "last_uid": 1} for name in ("Archive", "INBOX")}
    with system_context(reason="test imap removed legacy policy"):
        channel.cursor = {"delivery_mode": "new_only", "source_identity": "legacy", "mailboxes": positions}
        channel.save(update_fields=["cursor"])
        first = open_stream(
            channel,
            "messages",
            "INBOX",
            channel.backend,
            definition=StreamDefinition(key="messages", partition="INBOX"),
        )
        assert first.cursor == positions["INBOX"]
        assert channel.config["delivery_mode"] == "new_only"
        assert channel.cursor == {"mailboxes": positions}
        channel.config.pop("delivery_mode")
        channel.save(update_fields=["config"])
        account.folders["Archive"] = _folder(
            _eml(message_id="<retained-archive@example.com>"),
            _eml(message_id="<archive-arrival@example.com>"),
        )
        account.folders["New"] = _folder(_eml(message_id="<new-mailbox-history@example.com>"))

        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 2

        channel.refresh_from_db()
        assert "delivery_mode" not in channel.config
        assert channel.cursor == {"mailboxes": positions}
        assert set(Message._base_manager.values_list("external_id", flat=True)) == {
            "archive-arrival@example.com",
            "new-mailbox-history@example.com",
        }


def test_legacy_imap_seed_hooks_only_translate_state() -> None:
    """Seeding never writes, mutates the legacy input, or installs adapter state."""

    legacy = {
        "delivery_mode": "new_only",
        "source_identity": "legacy-account",
        "mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 1}},
    }
    bridge = SimpleNamespace(config={"host": "192.0.2.10"}, cursor=legacy)
    stream = SimpleNamespace(partition="INBOX")
    backend = ImapChannelBackend(bridge)
    config, retained = backend.seed_config(legacy)
    assert config == {
        "delivery_mode": "new_only",
        "source_identity": "legacy-account",
        "mailbox_selection": ["INBOX"],
    }
    assert retained == {"mailboxes": legacy["mailboxes"]}
    assert legacy["delivery_mode"] == "new_only"
    assert legacy["source_identity"] == "legacy-account"
    cursor = backend.seed_cursor(stream, retained)
    assert cursor == {"uidvalidity": 100, "last_uid": 1}
    assert cursor is not legacy["mailboxes"]["INBOX"]
    assert bridge.config == {"host": "192.0.2.10"}
    assert backend.delivery_boundary() == {}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("existing_policy", [{}, {"delivery_mode": "new_only", "source_identity": "configured"}])
def test_legacy_imap_cutover_preserves_config(composed_tables: None, existing_policy: dict[str, Any]) -> None:
    channel = _imap_channel(**existing_policy)
    original_config = dict(channel.config)
    with system_context(reason="test imap cutover setup"):
        channel.cursor = {
            "delivery_mode": "new_only",
            "source_identity": "legacy",
            "mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 1}},
        }
        channel.save(update_fields=["cursor"])
    with system_context(reason="test imap cutover"):
        stream = open_stream(
            channel,
            "messages",
            "INBOX",
            channel.backend,
            definition=StreamDefinition(key="messages", partition="INBOX"),
        )
        saved = Channel._base_manager.get(pk=channel.pk)
        assert saved.config == {
            **original_config,
            "delivery_mode": "new_only",
            "source_identity": existing_policy.get("source_identity", "legacy"),
            "mailbox_selection": ["INBOX"],
        }
        assert stream.cursor == {"uidvalidity": 100, "last_uid": 1}
        assert stream.config == {}
        assert saved.cursor == channel.cursor == {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 1}}}


@pytest.mark.django_db(transaction=True)
def test_new_mail_boundary_preserves_legacy_exclusion(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeated starting-point action must not exclude arrivals after cutover."""

    account = FakeImapAccount(
        {"INBOX": _folder(_eml(message_id="<old@example.com>"), _eml(message_id="<new@example.com>"))}
    )
    _wire_fake(monkeypatch, account)
    channel = _imap_channel()
    with system_context(reason="test imap legacy boundary replay"):
        backend = channel.backend
        try:
            backend.streams()
            identity = backend._source_identity_digest()
        finally:
            backend.close()
        channel.cursor = {
            "delivery_mode": "new_only",
            "source_identity": identity,
            "mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 1}},
        }
        channel.save(update_fields=["cursor"])
        boundary = backend.prepare_new_mail_boundary()
        assert not boundary.changed
        assert boundary.cursors == {"INBOX": {"uidvalidity": 100, "last_uid": 1}}
        channel.refresh_from_db()
        assert channel.config["delivery_mode"] == "new_only"
        assert all(stream.config == {} for stream in boundary.streams)
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 1
    assert set(Message._base_manager.values_list("external_id", flat=True)) == {"new@example.com"}


@pytest.mark.django_db(transaction=True)
def test_channel_sync_partitions_mailboxes(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Independent mailbox streams retain their own cursors without replica links."""

    del composed_tables
    account = FakeImapAccount(
        {
            "INBOX": _folder(_eml(message_id="<in-1@x>", subject="One", body="Inbox body\n")),
            "Archive": _folder(_eml(message_id="<ar-1@x>", subject="Two", body="Archive body\n")),
        }
    )
    _wire_fake(monkeypatch, account)
    channel = _imap_channel(sync_parallelism=2, own_addresses=["ada@example.com"])

    with system_context(reason="test imap partition drain"):
        backend = channel.backend
        try:
            definitions = backend.streams()
            assert {definition.partition for definition in definitions} == {"INBOX", "Archive"}
            stream = SyncStream.objects.current(channel, "messages", "Archive")
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
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long but valid Message-ID lands unchanged as the message/thread key."""

    del composed_tables
    long_message_id = f"outlook-{'x' * 700}@example.com"
    account = FakeImapAccount(
        {"INBOX": _folder(_eml(message_id=f"<{long_message_id}>", subject="", body="No subject.\n"))}
    )
    _wire_fake(monkeypatch, account)
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
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long RFC-5322 display names and Content-IDs land without truncation."""

    del composed_tables
    long_display_name = "Ada " + ("Lovelace " * 80).strip()
    long_cid = f"inline-{'x' * 700}@example.com"
    assert len(long_display_name) > 256
    assert len(long_cid) > 256
    assert Handle._meta.get_field("display_name").max_length >= 4096
    assert Part._meta.get_field("cid").max_length >= 4096

    account = FakeImapAccount(
        {
            "INBOX": _folder(
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
    _wire_fake(monkeypatch, account)
    channel = _imap_channel(batch_size=1)

    with system_context(reason="test imap wide headers"):
        landed = channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert landed == 1
    handle = Handle._base_manager.get(value="ada@example.com")
    part = Part._base_manager.get(message__external_id="wide-headers@x", type="text/html")
    assert handle.display_name == long_display_name
    assert part.cid == long_cid


@pytest.mark.django_db(transaction=True)
def test_channel_sync_lands_threads_parts_and_attachments(
    composed_tables: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run drains the mailbox through ingest: threading, roles, files, cursor."""

    del composed_tables
    reply_body = "Yes, confirmed!\n\n> Are we still on for Thursday?\n\n-- \nBob\n"
    long_attachment_name = f"{'x' * 588}.txt"
    account = FakeImapAccount(
        {
            "INBOX": _folder(
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
    _wire_fake(monkeypatch, account)
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
    assert SyncStream.objects.current(channel, "messages", "INBOX").cursor == {"uidvalidity": 100, "last_uid": 3}
    assert channel.last_sync_status == "ok"
    assert channel.last_sync_items == 3
    assert channel.sync_stage == Channel.SyncStage.COMPLETED
    assert channel.sync_progress["stage"] == Channel.SyncStage.COMPLETED
    assert channel.sync_progress["message"] == "Applied stream page"
    assert channel.sync_progress["details"]["backend"] == "ImapChannelBackend"
    assert channel.sync_progress["details"]["partition"] == "INBOX"
    assert "batch_size" not in channel.sync_progress["details"]
    assert channel.sync_progress["details"]["landed"] == 3
    assert channel.next_sync_at is not None


@pytest.mark.django_db(transaction=True)
def test_attributed_quote_reuses_the_original_body_fragment(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
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
    account = FakeImapAccount(
        {
            "INBOX": _folder(
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
    _wire_fake(monkeypatch, account)
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


@pytest.mark.django_db(transaction=True)
def test_channel_sync_dedups_retained_header_fragments(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A List-Id lands as a lowercased HEADER part; the shared value is ONE fragment.

    Retained envelope headers are content-addressed like body text, so a mailing
    list's ``List-Id`` repeated across the whole list dedups to a single fragment
    row referenced by each message's HEADER part.
    """

    del composed_tables
    list_id = "Dev list <dev.example.com>"
    account = FakeImapAccount(
        {
            "INBOX": _folder(
                _eml(message_id="<l1@x>", subject="News 1", extra_headers=f"List-Id: {list_id}"),
                _eml(message_id="<l2@x>", subject="News 2", extra_headers=f"List-Id: {list_id}"),
            )
        }
    )
    _wire_fake(monkeypatch, account)
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
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second run fetches only new UIDs; a UIDVALIDITY reset converges without dupes."""

    del composed_tables
    account = FakeImapAccount(
        {
            "INBOX": _folder(
                _eml(message_id="<a@x>", subject="A", body="Alpha.\n"),
                _eml(message_id="<b@x>", subject="B", body="Beta.\n"),
            )
        }
    )
    _wire_fake(monkeypatch, account)
    channel = _imap_channel()

    with system_context(reason="test imap incremental sync"):
        assert channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC)) == 2

        # New mail arrives; only UID 3 downloads.
        account.folders["INBOX"]["messages"][3] = {"raw": _eml(message_id="<c@x>", subject="C", body="Gamma.\n")}
        account.fetches.clear()
        assert channel.run_sync(now=datetime(2026, 7, 2, 12, 5, tzinfo=UTC)) == 1
        assert all(uids == (3,) for _folder_name, uids, _data in account.fetches)
        assert Message._base_manager.count() == 3

        # The server regenerates the mailbox: same messages, new UID space.
        account.folders["INBOX"]["uidvalidity"] = 999
        account.folders["INBOX"]["messages"] = {
            101: {"raw": _eml(message_id="<a@x>", subject="A", body="Alpha.\n")},
            102: {"raw": _eml(message_id="<b@x>", subject="B", body="Beta.\n")},
            103: {"raw": _eml(message_id="<c@x>", subject="C", body="Gamma.\n")},
        }
        channel.refresh_from_db()
        channel.run_sync(now=datetime(2026, 7, 2, 12, 10, tzinfo=UTC))

    assert Message._base_manager.count() == 3  # refetch converged, no duplicates
    channel.refresh_from_db()
    stream = SyncStream.objects.current(channel, "messages", "INBOX")
    assert stream.cursor == {"uidvalidity": 999, "last_uid": 103}
    assert stream.generation == 2
    assert RecordLink._base_manager.count() == 0


@pytest.mark.django_db(transaction=True)
def test_failed_run_never_persists_the_cursor(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that dies after fetching keeps the old cursor, so nothing is skipped."""

    del composed_tables
    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<a@x>"))})
    _wire_fake(monkeypatch, account)
    channel = _imap_channel()

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("ingest died")

    monkeypatch.setattr(type(Message.objects), "ingest", explode)
    with system_context(reason="test imap failed sync"), pytest.raises(RuntimeError):
        channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    channel.refresh_from_db()
    assert SyncStream.objects.current(channel, "messages", "INBOX").cursor == {}
    assert Message._base_manager.count() == 0
    assert channel.last_sync_status == "error"
    assert channel.sync_stage == Channel.SyncStage.FAILED
    assert channel.sync_error == "Integration operation failed."
    assert channel.sync_progress["stage"] == Channel.SyncStage.FAILED
    assert channel.sync_progress["details"]["backend"] == "imap"
    assert channel.sync_progress["details"]["mailbox"] == "INBOX"


@pytest.mark.django_db(transaction=True)
def test_failed_run_keeps_successfully_ingested_batch_cursor(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later batch failure resumes after already-landed messages."""

    del composed_tables
    account = FakeImapAccount(
        {
            "INBOX": _folder(
                _eml(message_id="<a@x>", subject="A"),
                _eml(message_id="<b@x>", subject="B"),
                _eml(message_id="<c@x>", subject="C"),
            )
        }
    )
    _wire_fake(monkeypatch, account)
    channel = _imap_channel(batch_size=2)
    manager_type = type(Message.objects)
    original_ingest = manager_type.ingest
    calls = 0

    def fail_second_batch(manager: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("second batch died")
        return original_ingest(manager, *args, **kwargs)

    monkeypatch.setattr(manager_type, "ingest", fail_second_batch)
    with system_context(reason="test imap partial sync"), pytest.raises(RuntimeError):
        channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert Message._base_manager.count() == 2
    channel.refresh_from_db()
    assert SyncStream.objects.current(channel, "messages", "INBOX").cursor == {"uidvalidity": 100, "last_uid": 2}
    assert channel.last_sync_status == "error"
    assert channel.sync_error == "Integration operation failed."


@pytest.mark.django_db(transaction=True)
def test_failed_second_record_rolls_back_the_whole_page(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later infrastructure failure in one page rolls back its messages and cursor."""

    del composed_tables
    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<a@x>"), _eml(message_id="<b@x>"))})
    _wire_fake(monkeypatch, account)
    channel = _imap_channel(batch_size=2)
    manager_type = type(Message.objects)
    ingest = manager_type.ingest
    calls = 0

    def fail_second(manager: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second record failed")
        return ingest(manager, *args, **kwargs)

    monkeypatch.setattr(manager_type, "ingest", fail_second)
    with system_context(reason="test imap page rollback"), pytest.raises(RuntimeError, match="second record"):
        channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))
    assert Message._base_manager.count() == 0
    assert SyncStream.objects.current(channel, "messages", "INBOX").cursor == {}


@pytest.mark.django_db(transaction=True)
def test_page_closure_resolves_quotes_of_a_later_record(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original and its later quoted-only sharer link after both page rows land."""

    del composed_tables
    paragraph = "Please retain this complete paragraph for our meeting tomorrow."
    account = FakeImapAccount(
        {
            "INBOX": _folder(
                _eml(message_id="<original@x>", subject="Meeting", body=paragraph + "\n"),
                _eml(message_id="<quoted@x>", subject="Re: Meeting", body="Agreed.\n\n> " + paragraph + "\n"),
            )
        }
    )
    _wire_fake(monkeypatch, account)
    channel = _imap_channel(batch_size=2)
    with system_context(reason="test imap page quotation closure"):
        assert channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC)) == 2
    original = Message._base_manager.get(external_id="original@x")
    quoted = Message._base_manager.get(external_id="quoted@x")
    assert Part._base_manager.filter(message=quoted, role=Part.PartRole.QUOTED).exists()
    assert MessageEdge._base_manager.filter(src=original, dst=quoted, kind="quote").exists()
