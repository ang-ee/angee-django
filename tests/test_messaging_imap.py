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
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.messaging_integrate_imap.backend import ImapChannelBackend, ImapError
from angee.messaging_integrate_imap.parser import (
    fallback_message,
    html_to_text,
    parse_message,
    split_plain_text,
    synthetic_external_id,
)
from tests.conftest import _clear_model_tables, _create_missing_tables, make_integration
from tests.test_messaging import (
    MESSAGING_TEST_MODELS,
    Handle,
    Message,
    Part,
    Participant,
    Thread,
    _storage_drive,
)
from tests.test_messaging_graphql import Channel

IMAP_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel)

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
        extra_headers=(
            f"In-Reply-To: <{long_message_id}>\r\n"
            f"References: <root@example.com> <{long_message_id}>"
        ),
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
    """A forwarded message/rfc822 part keeps its raw bytes and a subject filename."""

    inner = _eml(message_id="<inner@example.com>", subject="Inner report")
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
            (folder.get("flags", (b"\\HasNoChildren",)), b"/", name)
            for name, folder in self.account.folders.items()
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
        if isinstance(criteria, (list, tuple)) and criteria and criteria[0] == "UID":
            start = int(str(criteria[1]).split(":", 1)[0])
            matched = [uid for uid in uids if uid >= start]
            # RFC 3501: a UID range of ``n:*`` returns the highest-UID message
            # even when every UID is below ``n``.
            return matched or uids[-1:]
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
    return ImapChannelBackend(bridge)


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
    while batch := backend.fetch_messages():
        collected.extend(batch)
    return collected


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

    first = backend.fetch_messages()
    second = backend.fetch_messages()
    third = backend.fetch_messages()

    assert [message.subject for message in first] == ["A", "B"]
    assert [message.subject for message in second] == ["C"]
    assert third == []
    assert account.selects == ["INBOX"]  # Junk never opened
    assert backend.bridge.cursor["mailboxes"]["INBOX"] == {"uidvalidity": 100, "last_uid": 3}
    assert account.logouts == 1
    assert account.logins == [("login", "ada@example.com", "pw")]


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
    assert backend.bridge.cursor["mailboxes"] == {
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

    assert backend.fetch_messages() == []
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
    assert backend.bridge.cursor["mailboxes"]["INBOX"]["last_uid"] == 3


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

    assert backend.fetch_messages() == []
    assert account.fetches == []  # the echoed max-UID (2) was filtered, nothing fetched


def test_uidvalidity_change_resets_the_folder_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A regenerated mailbox refetches from scratch under its new UID space."""

    account = FakeImapAccount(
        {"INBOX": _folder(_eml(message_id="<a@x>", subject="A"), uidvalidity=777)}
    )
    backend = _backend(monkeypatch, account)
    backend.bridge.cursor = {"mailboxes": {"INBOX": {"uidvalidity": 100, "last_uid": 50}}}

    messages = _drain(backend)

    assert [message.subject for message in messages] == ["A"]
    assert account.searches == [("INBOX", "ALL")]
    assert backend.bridge.cursor["mailboxes"]["INBOX"] == {"uidvalidity": 777, "last_uid": 1}


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
        backend.fetch_messages()

    backend = _backend(monkeypatch, account, config={"security": "carrier-pigeon"})
    with pytest.raises(ImapError, match="security"):
        backend.fetch_messages()

    class _SshCredential:
        kind = CredentialKind.SSH_KEY
        external_account = None

    backend = _backend(monkeypatch, account, credential=_SshCredential())
    with pytest.raises(ImapError, match="credential"):
        backend.fetch_messages()

    backend = _backend(monkeypatch, account)
    backend.bridge.credential = None
    with pytest.raises(ImapError, match="credential"):
        backend.fetch_messages()


def test_host_is_judged_by_the_outbound_address_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private self-hosted mail is allowed; metadata/link-local escapes never are."""

    account = FakeImapAccount({"INBOX": _folder()})
    backend = _backend(monkeypatch, account, config={"host": "169.254.169.254"})
    with pytest.raises(ImapError, match="forbidden"):
        backend.fetch_messages()

    # A private (RFC 1918) host is the legitimate self-hosted case and connects.
    backend = _backend(monkeypatch, account, config={"host": "10.0.0.4"})
    assert backend.fetch_messages() == []
    assert account.logins  # the private host authenticated


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
    backend.fetch_messages()

    assert isinstance(captured["ssl_context"], ssl.SSLContext)
    assert captured["ssl"] is True


# --- end to end: Channel.run_sync over real tables ---


@pytest.fixture
def imap_tables() -> Iterator[None]:
    """Create the concrete messaging tables plus the Channel child."""

    created_models = _create_missing_tables(IMAP_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(IMAP_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


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


def _wire_fake(monkeypatch: pytest.MonkeyPatch, account: FakeImapAccount) -> None:
    """Point the imap backend at the in-memory account."""

    monkeypatch.setattr(FakeIMAPClient, "account", account, raising=False)
    monkeypatch.setattr(ImapChannelBackend, "client_class", FakeIMAPClient)


@pytest.mark.django_db(transaction=True)
def test_channel_sync_partitions_mailboxes(
    imap_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mailboxes are the partition units and a partition drain stays in its lane.

    The threaded fan-out itself is Postgres-only (`Channel._sync_parallelism`
    pins other vendors serial — SQLite cannot take concurrent writers), so this
    exercises the partition contract single-threaded: enumeration, the
    per-partition plan filter, the row-locked cursor-slice merge, and the serial
    fallback landing every mailbox's mail with a merged cursor.
    """

    del imap_tables
    account = FakeImapAccount(
        {
            "INBOX": _folder(_eml(message_id="<in-1@x>", subject="One", body="Inbox body\n")),
            "Archive": _folder(_eml(message_id="<ar-1@x>", subject="Two", body="Archive body\n")),
        }
    )
    _wire_fake(monkeypatch, account)
    channel = _imap_channel(sync_parallelism=2, own_addresses=["ada@example.com"])

    assert set(channel.backend.sync_partitions()) == {"INBOX", "Archive"}

    with system_context(reason="test imap partition drain"):
        # One partition drained in isolation touches only its own mailbox and
        # persists only its own cursor slice.
        landed_archive = channel._drain_partition("Archive")
        channel.refresh_from_db()
        assert landed_archive == 1
        assert set(channel.cursor["mailboxes"]) == {"Archive"}

        # The full sync (serial on SQLite) still lands the rest and merges the
        # INBOX watermark beside the already-persisted Archive slice.
        landed = channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert landed == 1
    assert Message._base_manager.count() == 2
    channel.refresh_from_db()
    mailboxes = channel.cursor["mailboxes"]
    assert set(mailboxes) == {"INBOX", "Archive"}
    assert all(int(entry["last_uid"]) >= 1 for entry in mailboxes.values())


@pytest.mark.django_db(transaction=True)
def test_channel_sync_preserves_overlong_message_id(
    imap_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long but valid Message-ID lands unchanged as the message/thread key."""

    del imap_tables
    long_message_id = f"outlook-{'x' * 700}@example.com"
    account = FakeImapAccount(
        {
            "INBOX": _folder(
                _eml(message_id=f"<{long_message_id}>", subject="", body="No subject.\n")
            )
        }
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
    imap_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long RFC-5322 display names and Content-IDs land without truncation."""

    del imap_tables
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
    imap_tables: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run drains the mailbox through ingest: threading, roles, files, cursor."""

    del imap_tables
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
    assert channel.cursor["mailboxes"]["INBOX"] == {"uidvalidity": 100, "last_uid": 3}
    assert channel.last_sync_status == "ok"
    assert channel.last_sync_items == 3
    assert channel.sync_stage == Channel.SyncStage.COMPLETED
    assert channel.sync_progress["stage"] == Channel.SyncStage.COMPLETED
    assert channel.sync_progress["message"] == "Ingested message batch"
    assert channel.sync_progress["details"]["backend"] == "ImapChannelBackend"
    assert channel.sync_progress["details"]["mailbox"] == "INBOX"
    assert channel.sync_progress["details"]["batch_size"] == 1
    assert channel.sync_progress["details"]["landed"] == 3
    assert channel.next_sync_at is not None


@pytest.mark.django_db(transaction=True)
def test_attributed_quote_reuses_the_original_body_fragment(
    imap_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully library-segmented reply keeps the content-addressed quote link.

    Attribution header, marker-quoted paragraph, and a salutation signature (no
    ``--`` delimiter): the stripped quoted paragraph must hash to the same
    Fragment row as the root message's body after one channel sync.
    """

    del imap_tables
    reply_body = (
        "Yes, confirmed!\n"
        "\n"
        "On Thu, Jul 2, 2026 Ada wrote:\n"
        "> Are we still on for Thursday?\n"
        "\n"
        "Best regards,\n"
        "Bob\n"
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
    imap_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A List-Id lands as a lowercased HEADER part; the shared value is ONE fragment.

    Retained envelope headers are content-addressed like body text, so a mailing
    list's ``List-Id`` repeated across the whole list dedups to a single fragment
    row referenced by each message's HEADER part.
    """

    del imap_tables
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
        Part._base_manager.select_related("fragment")
        .filter(role=Part.PartRole.HEADER)
        .order_by("message_id")
    )
    assert [part.name for part in headers] == ["list-id", "list-id"]
    assert headers[0].fragment.text == list_id
    assert headers[0].fragment.kind == "header"
    # Both messages point at the same content-addressed fragment row.
    assert headers[0].fragment_id == headers[1].fragment_id


@pytest.mark.django_db(transaction=True)
def test_channel_resync_is_incremental_and_idempotent(
    imap_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second run fetches only new UIDs; a UIDVALIDITY reset converges without dupes."""

    del imap_tables
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
    assert channel.cursor["mailboxes"]["INBOX"] == {"uidvalidity": 999, "last_uid": 103}


@pytest.mark.django_db(transaction=True)
def test_failed_run_never_persists_the_cursor(
    imap_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that dies after fetching keeps the old cursor, so nothing is skipped."""

    del imap_tables
    account = FakeImapAccount({"INBOX": _folder(_eml(message_id="<a@x>"))})
    _wire_fake(monkeypatch, account)
    channel = _imap_channel()

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("ingest died")

    monkeypatch.setattr(Message.objects, "ingest", explode)
    with system_context(reason="test imap failed sync"), pytest.raises(RuntimeError):
        channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    channel.refresh_from_db()
    assert channel.cursor == {}  # in-memory advance was never persisted
    assert channel.last_sync_status == "error"
    assert channel.sync_stage == Channel.SyncStage.FAILED
    assert channel.sync_error == "RuntimeError: ingest died"
    assert channel.sync_progress["stage"] == Channel.SyncStage.FAILED
    assert channel.sync_progress["details"]["backend"] == "imap"
    assert channel.sync_progress["details"]["mailbox"] == "INBOX"


@pytest.mark.django_db(transaction=True)
def test_failed_run_keeps_successfully_ingested_batch_cursor(
    imap_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later batch failure resumes after already-landed messages."""

    del imap_tables
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
    original_ingest = Message.objects.ingest
    calls = 0

    def fail_second_batch(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second batch died")
        return original_ingest(*args, **kwargs)

    monkeypatch.setattr(Message.objects, "ingest", fail_second_batch)
    with system_context(reason="test imap partial sync"), pytest.raises(RuntimeError):
        channel.run_sync(now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

    assert Message._base_manager.count() == 2
    channel.refresh_from_db()
    assert channel.cursor["mailboxes"]["INBOX"] == {"uidvalidity": 100, "last_uid": 2}
    assert channel.last_sync_status == "error"
    assert channel.sync_error == "RuntimeError: second batch died"
