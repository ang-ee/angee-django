"""IMAP channel backend: incremental mailbox sync over IMAPClient.

Transport + cursor only — parsing is :mod:`.parser`, and the idempotent map onto
threads/messages/parts is ``Message.objects.ingest``. The sync is strictly
read-only against the server: every folder is opened read-only and bodies are
fetched with ``BODY.PEEK``, so no ``\\Seen`` flag is ever set by a sync.

Incremental state lives on one ``SyncStream.cursor`` per mailbox::

    {"uidvalidity": 123456, "last_uid": 4211}

An operator may establish a future-only boundary while the channel is paused.
That operator intent belongs to ``bridge.config``; legacy intent is retained in
``SyncStream.config`` during seeding. Epoch resets preserve both policy owners.

Correctness rests on three facts. UIDVALIDITY is checked every run: a changed
value raises ``CursorInvalid`` and starts a new stream generation. A normal
historical stream refetches in full; the channel-scoped Message identity dedups
the replay. A future-only stream captures a fresh UIDNEXT boundary on epoch
change, preserving its explicit exclusion of history. UIDNEXT pre-screens each
unchanged mailbox so an idle folder costs one round-trip. The backend advances
its cursor in memory; the generic driver commits it with the corresponding
messages. A crash can re-fetch an unfinished batch without skipping mail or
restarting a completed backfill.

``extract`` follows the stream's paging contract (one bounded batch per
call — ``config["batch_size"]``, default 200, with body pulls additionally split
under a ``config["max_batch_bytes"]`` budget). A message over ``config
["max_message_bytes"]`` lands header-only with a truncation marker; a message the
MIME layer rejects lands through the parser's fallback envelope — mail is never
dropped, and UIDs the server fails to answer for are logged rather than silently
skipped. Authentication draws on the channel's credential: ``basic_auth`` logs in
with username/password, ``oauth`` refreshes then presents the access token over
XOAUTH2 (Gmail, Outlook). The operator-supplied host passes the shared outbound
address judgement under the operator-configured-connection policy
(``integrate.net``): self-hosted private hosts work, metadata escapes never do.
"""

from __future__ import annotations

import logging
import ssl
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from email import policy
from email.parser import BytesHeaderParser
from functools import partial
from hashlib import sha256
from typing import Any, TypeVar

from django.core.exceptions import ValidationError
from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientAbortError, LoginError
from pydantic import BaseModel, ConfigDict

from angee.base.db import get_write_alias, related_on
from angee.integrate.credentials import CredentialKind
from angee.integrate.errors import IntegrationError
from angee.integrate.net import is_unsafe_address, resolved_addresses
from angee.integrate.streams import CursorInvalid, StreamDefinition, StreamPage, open_stream, reset_stream
from angee.integrate.sync import current_bridge_progress
from angee.messaging.backends import ParsedMessage
from angee.messaging.email import AnymailEmailChannelBackend
from angee.messaging_integrate_imap.parser import fallback_message, parse_message

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_TRANSIENT_ERRORS = (OSError, EOFError, IMAPClientAbortError)
_DEFAULT_BATCH_SIZE = 200
_DEFAULT_MAX_MESSAGE_BYTES = 50_000_000
_DEFAULT_MAX_BATCH_BYTES = 64_000_000
_DEFAULT_TIMEOUT_SECONDS = 60
MAX_SAMPLE_MESSAGES = 50
MAX_SAMPLE_BYTES = 64_000_000
NEW_MAIL_DELIVERY_MODE = "new_only"
# Folders never worth syncing by default; SPECIAL-USE flags are authoritative,
# these casefolded names are the fallback for servers that do not advertise them.
_SKIP_SPECIAL_USE = frozenset({"\\junk", "\\trash", "\\drafts"})
_SKIP_FOLDER_NAMES = frozenset({"junk", "spam", "trash", "drafts", "deleted items", "deleted messages"})


class ImapError(IntegrationError):
    """Raised when the channel's IMAP configuration, transport, or login is unusable.

    An ``IntegrationError``: its message is composed here from facts the
    operator already owns (host and login name), never a vendor exception
    payload, so sync telemetry and the connection test may
    show it verbatim.
    """


class ImapSampleMessage(BaseModel):
    """Bounded header preview; a UID is meaningful only within its mailbox epoch."""

    model_config = ConfigDict(frozen=True)
    uid: int
    subject: str
    sender: str
    sent_at: str
    size: int
    flags: list[str]


class ImapSamplePreviewRequest(BaseModel):
    """One mailbox scope and its optional explicit snapshot continuation.

    Continuations carry the preceding page's ``total_count``. The backend counts
    the selection once, then only subtracts UIDs confirmed expunged between that
    page's SEARCH and FETCH; it never silently recounts a changing mailbox.
    """

    model_config = ConfigDict(frozen=True)
    mailbox: str
    since: date | None = None
    before: date | None = None
    all_dates: bool = False
    uidvalidity: int | None = None
    upper_uid: int | None = None
    before_uid: int | None = None
    total_count: int | None = None
    limit: int = 20


class ImapSamplePreview(BaseModel):
    """Explicit remote selection; previewing does not move the sync cursor.

    ``total_count`` is the first SEARCH's count less confirmed fetch-time
    expunges encountered so far. Unobserved changes do not rewrite that count.
    """

    model_config = ConfigDict(frozen=True)
    mailbox: str
    uidvalidity: int
    upper_uid: int
    total_count: int
    next_before_uid: int | None
    messages: list[ImapSampleMessage]


class ImapSampleImport(BaseModel):
    """Local landing receipt for a bounded, historical import."""

    model_config = ConfigDict(frozen=True)
    message_ids: list[str]
    requested_uids: list[int]
    imported_uids: list[int]
    missing_uids: list[int]
    flags_unchanged: bool


@dataclass(frozen=True)
class ImapDeliveryBoundary:
    """An account binding and one retained future-only cursor per mailbox."""

    source_identity: str
    cursors: dict[str, dict[str, Any]]
    streams: tuple[Any, ...]
    changed: bool


@dataclass
class _MailboxWork:
    """One selected mailbox's remaining fetch plan for the current run."""

    name: str
    uidvalidity: int
    uids: list[int] = field(default_factory=list)

    def take(self, count: int) -> list[int]:
        """Remove and return the next ``count`` UIDs of this mailbox's plan."""

        chunk = self.uids[:count]
        del self.uids[: len(chunk)]
        return chunk


class ImapChannelBackend(AnymailEmailChannelBackend):
    """Sync an IMAP account's mailboxes into messaging, one bounded batch at a time.

    ``config`` keys: ``host`` (required), ``port`` (defaults per security),
    ``security`` (``ssl`` default / ``starttls`` / ``plain``), ``username``
    (defaults to the credential's username or connected account email),
    ``mailboxes`` (explicit include list; default prefers the ``\\All``
    special-use folder, else everything selectable minus junk/trash/drafts),
    ``skip_mailboxes``, ``own_addresses`` (direction detection), ``batch_size``,
    ``max_message_bytes``, ``max_batch_bytes``, ``timeout``.
    """

    key = "imap"
    label = "IMAP"
    icon = "mail"
    defaults = {"vendor": "imap"}

    client_class = IMAPClient
    """The protocol client factory — a seam so tests substitute an in-memory server."""

    def __init__(self, integration: object) -> None:
        """Bind to the channel row and start with no in-run paging state."""

        super().__init__(integration)
        self._client: IMAPClient | None = None
        self._selected = ""
        self._login_name = ""
        self._work: deque[_MailboxWork] | None = None
        self._own_addresses: frozenset[str] = frozenset()
        self._stream_identity: tuple[str, int] | None = None
        self._stream: Any = None
        self._cursor: dict[str, Any] = {}
        self._credential: Any = None
        self._external_account: Any = None

    def _load_credentials(self, *, using: str) -> None:
        """Reload transport authentication through the operation's explicit alias."""

        self._credential = related_on(self.bridge, "credential", using=using, required=False)
        self._external_account = (
            related_on(self._credential, "external_account", using=using, required=False)
            if self._credential is not None
            else None
        )

    def extract(
        self, stream: Any, page_bound: int, *, deadline: float | None = None, using: str | None = None
    ) -> StreamPage:
        """Fetch a bounded mailbox page without mutating its durable cursor."""

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self._load_credentials(using=using)
        self._stream = stream
        identity = (stream.partition, stream.generation)
        if self._stream_identity != identity:
            self._stream_identity = identity
            self._cursor = deepcopy(stream.cursor)
            self._work = self._discover(stream.partition)
        batch_size = min(max(1, page_bound), self._batch_size())
        while self._work:
            work = self._work[0]
            chunk = work.take(batch_size)
            if not chunk:
                self._work.popleft()
                continue
            messages = self._fetch_chunk(work, chunk)
            answered = {
                int(message.metadata["uid"])
                for message in messages
                if isinstance(message.metadata, dict) and message.metadata.get("uid") is not None
            }
            self._confirm_expunged(
                work.name,
                work.uidvalidity,
                set(chunk) - answered,
                retry_hint="Retry the sync before advancing its cursor.",
            )
            self._cursor.update(uidvalidity=work.uidvalidity, last_uid=chunk[-1])
            if messages:
                return StreamPage(records=messages, cursor=deepcopy(self._cursor), exhausted=not work.uids)
        return StreamPage(records=(), cursor=deepcopy(self._cursor), exhausted=True)

    def _sample_mailbox(self, mailbox: str, *, uidvalidity: int | None = None) -> int:
        """Open an explicitly selected configured folder read-only and pin its epoch."""

        client = self._client if self._client is not None else self._connect()
        if not mailbox or mailbox not in self._select_mailboxes(client):
            raise ValidationError("Choose a mailbox included in this channel's configuration.")
        self._select(mailbox)
        current = int(client.folder_status(mailbox, [b"UIDVALIDITY"])[b"UIDVALIDITY"])
        if uidvalidity is not None and current != uidvalidity:
            raise ValidationError("The mailbox UID identity changed. Preview the messages again.")
        return current

    def preview_sample(self, request: ImapSamplePreviewRequest, *, using: str | None = None) -> ImapSamplePreview:
        """Read one page from a UID-frozen mailbox selection without moving its cursor."""

        if not 1 <= request.limit <= MAX_SAMPLE_MESSAGES:
            raise ValidationError(f"Choose a sample limit between 1 and {MAX_SAMPLE_MESSAGES}.")
        if request.all_dates:
            if request.since is not None or request.before is not None:
                raise ValidationError("Do not combine all-dates preview with a date window.")
            criteria: list[Any] = []
        else:
            if request.since is None or request.before is None or not 0 < (request.before - request.since).days <= 366:
                raise ValidationError("Choose a positive date window of at most one year.")
            criteria = ["SINCE", request.since, "BEFORE", request.before]
        continuation = any(
            value is not None
            for value in (
                request.uidvalidity,
                request.upper_uid,
                request.before_uid,
                request.total_count,
            )
        )
        if continuation and (
            request.uidvalidity is None
            or request.uidvalidity <= 0
            or request.upper_uid is None
            or request.upper_uid < 0
            or request.before_uid is None
            or not 0 < request.before_uid <= request.upper_uid
            or request.total_count is None
            or request.total_count < 0
        ):
            raise ValidationError("Preview this mailbox scope again before loading its next page.")
        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self._load_credentials(using=using)
        try:
            current_uidvalidity = self._sample_mailbox(
                request.mailbox,
                uidvalidity=request.uidvalidity,
            )
            client = self._client_or_fail()
            status = client.folder_status(request.mailbox, [b"UIDNEXT"])
            current_upper_uid = self._uidnext(request.mailbox, status) - 1
            snapshot_upper_uid = current_upper_uid if request.upper_uid is None else request.upper_uid
            if snapshot_upper_uid > current_upper_uid:
                raise ValidationError("The mailbox snapshot is no longer available. Preview it again.")
            page_upper_uid = (
                min(snapshot_upper_uid, request.before_uid - 1)
                if request.before_uid is not None
                else snapshot_upper_uid
            )
            # Never send 1:0: IMAP treats reversed UID ranges as inclusive too.
            criteria = ["UID", f"1:{page_upper_uid}", *criteria]
            found = (
                sorted(
                    (int(uid) for uid in client.search(criteria) if 0 < int(uid) <= page_upper_uid),
                    reverse=True,
                )
                if page_upper_uid > 0
                else []
            )
            chosen = found[: request.limit]
            rows = client.fetch(chosen, [b"BODY.PEEK[HEADER]", b"FLAGS", b"RFC822.SIZE"]) if chosen else {}
            answered = {uid: item for uid in chosen if (item := rows.get(uid)) is not None and b"BODY[HEADER]" in item}
            self._report_unanswered(request.mailbox, chosen, answered, phase="sample header fetch")
            confirmed_expunged = self._confirm_expunged(
                request.mailbox,
                current_uidvalidity,
                set(chosen) - set(answered),
                retry_hint="Retry the preview before advancing its page.",
            )
            total_count = len(found) if request.total_count is None else request.total_count
            next_before_uid = chosen[-1] if len(found) > len(chosen) else None
            messages = []
            for uid in chosen:
                item = answered.get(uid)
                if item is None:
                    continue
                raw = bytes(item[b"BODY[HEADER]"])
                if len(raw) > 1_000_000:
                    raise ValidationError("A message header is too large to preview safely.")
                header = BytesHeaderParser(policy=policy.default).parsebytes(raw)
                messages.append(
                    ImapSampleMessage(
                        uid=uid,
                        subject=str(header.get("Subject", "")),
                        sender=str(header.get("From", "")),
                        sent_at=str(header.get("Date", "")),
                        size=int(item.get(b"RFC822.SIZE", 0)),
                        flags=sorted(_text(flag) for flag in item.get(b"FLAGS", ())),
                    )
                )
            self._sample_mailbox(request.mailbox, uidvalidity=current_uidvalidity)
            return ImapSamplePreview(
                mailbox=request.mailbox,
                uidvalidity=current_uidvalidity,
                upper_uid=snapshot_upper_uid,
                total_count=max(total_count - len(confirmed_expunged), 0),
                next_before_uid=next_before_uid,
                messages=messages,
            )
        except CursorInvalid as error:
            raise ValidationError("The mailbox UID identity changed. Preview the messages again.") from error
        finally:
            self.close()

    def fetch_sample(
        self,
        *,
        mailbox: str,
        uidvalidity: int,
        uids: list[int],
        using: str | None = None,
    ) -> tuple[list[ParsedMessage], list[int], bool]:
        """Fetch the explicit UID set without touching the regular discovery/cursor path."""

        if type(uidvalidity) is not int or uidvalidity <= 0:
            raise ValidationError("Preview the mailbox before importing its messages.")
        if not uids or len(uids) > MAX_SAMPLE_MESSAGES or any(type(uid) is not int or uid <= 0 for uid in uids):
            raise ValidationError(f"Select between 1 and {MAX_SAMPLE_MESSAGES} positive message UIDs.")
        if len(set(uids)) != len(uids):
            raise ValidationError("Select each message UID once.")
        requested = sorted(uids)
        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self._load_credentials(using=using)
        try:
            self._sample_mailbox(mailbox, uidvalidity=uidvalidity)
            self._own_addresses = self._resolve_own_addresses()
            client = self._client_or_fail()
            prior = client.fetch(requested, [b"FLAGS", b"RFC822.SIZE"])
            if sum(int(item.get(b"RFC822.SIZE", 0)) for item in prior.values()) > MAX_SAMPLE_BYTES:
                raise ValidationError("The selected messages exceed 64 MB. Select a smaller sample.")
            work = _MailboxWork(name=mailbox, uidvalidity=uidvalidity)
            messages = self._fetch_chunk(work, requested)
            self._sample_mailbox(mailbox, uidvalidity=uidvalidity)
            after = self._client_or_fail().fetch(requested, [b"FLAGS"])
            unchanged = set(prior) == set(after) and all(
                set(prior[uid].get(b"FLAGS", ())) == set(after[uid].get(b"FLAGS", ())) for uid in prior
            )
            imported = sorted(int(message.metadata["uid"]) for message in messages)
            return messages, imported, unchanged
        except CursorInvalid as error:
            raise ValidationError("The mailbox UID identity changed. Preview the messages again.") from error
        finally:
            self.close()

    def streams(self, *, deadline: float | None = None, using: str | None = None) -> tuple[StreamDefinition, ...]:
        """Declare one event-feed partition per mailbox."""

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self._load_credentials(using=using)
        client = self._connect()
        names = sorted(self._select_mailboxes(client))
        return tuple(StreamDefinition(key="messages", partition=name) for name in names)

    def seed_cursor(self, stream: Any, legacy_cursor: dict[str, Any]) -> dict[str, Any] | None:
        """Translate the mailbox position and retain legacy policy on its first stream."""

        mailbox = (legacy_cursor.get("mailboxes") or {}).get(stream.partition)
        if not mailbox:
            return None
        if legacy_cursor.get("delivery_mode") == NEW_MAIL_DELIVERY_MODE:
            # Driver seeding holds the stream lock; retain policy across later epochs.
            using = get_write_alias(type(self.bridge), instance=self.bridge)
            stream.config = {
                **stream.config,
                "delivery_mode": NEW_MAIL_DELIVERY_MODE,
                "source_identity": legacy_cursor.get("source_identity", ""),
                "mailbox_selection": sorted(legacy_cursor["mailboxes"]),
            }
            stream.save(using=using, update_fields=["config"])
        return deepcopy(mailbox)

    def _report_progress(self, stage: str, message: str, **details: Any) -> None:
        """Publish IMAP-specific progress into the generic bridge reporter."""

        reporter = current_bridge_progress()
        if reporter is None:
            return
        reporter.report(stage, message=message, details={"backend": self.key, **details})

    # --- discovery ---

    def prepare_new_mail_boundary(self, *, using: str | None = None) -> ImapDeliveryBoundary:
        """Return a durable boundary that excludes every currently selected message.

        The read-only STATUS/SELECT pair pins each mailbox epoch and captures its
        next allocatable UID. A repeated call against the same selected set and
        epochs returns the retained boundary unchanged: losing the first action's
        response can never make a retry skip mail that arrived after it committed.

        The snapshot precedes opening any missing stream. The Channel action
        locks and validates its configuration before installing the boundaries;
        all remote IO has finished before that transaction begins.
        """

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self._load_credentials(using=using)
        try:
            client = self._connect()
            source_identity = self._source_identity_digest()
            selected = self._select_mailboxes(client)
            if not selected:
                raise ImapError("The IMAP channel has no selectable mailboxes.")
            snapshot: dict[str, dict[str, int]] = {}
            for name in selected:
                status = client.folder_status(name, [b"UIDVALIDITY", b"UIDNEXT"])
                uidvalidity = int(status[b"UIDVALIDITY"])
                uidnext = self._uidnext(name, status)
                selected_status = client.select_folder(name, readonly=True)
                self._selected = name
                selected_uidvalidity = int(selected_status[b"UIDVALIDITY"])
                if selected_uidvalidity != uidvalidity:
                    raise ImapError(
                        f"IMAP mailbox {name!r} changed UIDVALIDITY while setting its new-mail starting point."
                    )
                snapshot[name] = {
                    "uidvalidity": uidvalidity,
                    "last_uid": max(uidnext - 1, 0),
                }
        finally:
            self.close()

        try:
            streams = tuple(
                open_stream(self.bridge, "messages", partition, self, using=using) for partition in sorted(snapshot)
            )
        finally:
            self.close()
        retained = {stream.partition: stream.cursor for stream in streams}
        same_epochs = (
            self.bridge.config.get("delivery_mode") == NEW_MAIL_DELIVERY_MODE
            and self.bridge.config.get("source_identity") == source_identity
            and self.bridge.config.get("mailbox_selection") == sorted(snapshot)
            and all(retained[name].get("uidvalidity") == value["uidvalidity"] for name, value in snapshot.items())
        )
        return ImapDeliveryBoundary(source_identity, retained if same_epochs else snapshot, streams, not same_epochs)

    def apply_new_mail_boundary(self, boundary: ImapDeliveryBoundary, *, using: str | None = None) -> None:
        """Install mailbox boundaries through the driver's stream and epoch owners."""

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        for stream in boundary.streams:
            cursor = boundary.cursors[stream.partition]
            if stream.cursor != cursor:
                reset_stream(stream, cursor=cursor, using=using)

    def _discover(self, name: str) -> deque[_MailboxWork]:
        """Plan the selected partition after validating its mailbox epoch."""

        client = self._client if self._client is not None else self._connect()
        self._own_addresses = self._resolve_own_addresses()
        boundary = self.delivery_boundary()
        new_only = bool(boundary)
        if new_only and boundary.get("source_identity") != self._source_identity_digest():
            raise ImapError("The IMAP account changed. Pause the channel and set the starting point again.")
        selection = boundary.get("mailbox_selection", boundary.get("mailboxes", ()))
        if new_only and sorted(selection) != sorted(self._select_mailboxes(client)):
            raise ImapError("The selected IMAP mailboxes changed. Pause the channel and set the starting point again.")
        status = client.folder_status(name, [b"UIDVALIDITY", b"UIDNEXT"])
        uidvalidity = int(status[b"UIDVALIDITY"])
        uidnext = self._uidnext(name, status)
        retained = self._cursor.get("uidvalidity")
        if retained is not None and int(retained) != uidvalidity:
            self._invalidate_cursor(name, uidvalidity=uidvalidity, uidnext=uidnext)
        last_uid = int(self._cursor.get("last_uid", max(0, uidnext - 1) if new_only else 0))
        self._cursor.update(uidvalidity=uidvalidity, last_uid=last_uid)
        if last_uid and uidnext <= last_uid + 1:
            return deque()
        self._select(name, expected_uidvalidity=uidvalidity)
        criteria = ["UID", f"{last_uid + 1}:*"] if last_uid else "ALL"
        # RFC 3501 n:* can return the highest UID even when it lies below n.
        uids = sorted(int(uid) for uid in client.search(criteria) if int(uid) > last_uid)
        self._report_progress("discovering", "Planned IMAP mailbox sync", mailbox=name, queued_messages=len(uids))
        return deque([_MailboxWork(name=name, uidvalidity=uidvalidity, uids=uids)])

    def delivery_boundary(self, *, using: str | None = None) -> dict[str, Any]:
        """Read operator intent or legacy policy retained on this stream."""

        if self.bridge.config.get("delivery_mode") == NEW_MAIL_DELIVERY_MODE:
            return self.bridge.config
        if self._stream is not None and self._stream.config.get("delivery_mode") == NEW_MAIL_DELIVERY_MODE:
            return self._stream.config
        return {}

    def _invalidate_cursor(self, name: str, *, uidvalidity: int, uidnext: int | None = None) -> None:
        """Force a new epoch while preserving a future-only exclusion of history."""

        self._selected = ""
        cursor: dict[str, Any] = {}
        if self.delivery_boundary():
            if uidnext is None:
                uidnext = self._uidnext(name, self._client_or_fail().folder_status(name, [b"UIDNEXT"]))
            cursor = {"uidvalidity": uidvalidity, "last_uid": max(0, uidnext - 1)}
        raise CursorInvalid(cursor=cursor)

    def _select_mailboxes(self, client: IMAPClient) -> list[str]:
        """Return the mailbox names this channel syncs.

        An explicit ``config["mailboxes"]`` list wins verbatim (a missing name
        fails the run loudly rather than being skipped silently). Otherwise the
        ``\\All`` special-use folder alone when the server advertises one — it
        already contains every non-junk message once — else every selectable
        folder minus junk/trash/drafts. An operator's ``config["skip_mailboxes"]``
        outranks the ``\\All`` preference, so even Gmail's archive can be skipped.
        """

        config = self.bridge.config
        explicit = [str(name) for name in (config.get("mailboxes") or []) if str(name)]
        if explicit:
            return explicit
        skip_names = {str(name).casefold() for name in (config.get("skip_mailboxes") or [])}
        all_folder: str | None = None
        selected: list[str] = []
        for raw_flags, delimiter, name in client.list_folders():
            flags = {_text(flag).casefold() for flag in raw_flags}
            if "\\noselect" in flags:
                continue
            leaf = name.rsplit(_text(delimiter), 1)[-1] if delimiter else name
            if name.casefold() in skip_names or leaf.casefold() in skip_names:
                continue
            if flags & _SKIP_SPECIAL_USE:
                continue
            if "\\all" in flags:
                all_folder = name
                continue
            if name.casefold() in _SKIP_FOLDER_NAMES or leaf.casefold() in _SKIP_FOLDER_NAMES:
                continue
            selected.append(name)
        return [all_folder] if all_folder is not None else selected

    # --- fetching ---

    def _fetch_chunk(self, work: _MailboxWork, uids: list[int]) -> list[ParsedMessage]:
        """Fetch and parse one chunk: pin the mailbox, size-screen, budgeted body pulls."""

        self._select(work.name, expected_uidvalidity=work.uidvalidity)
        self._report_progress(
            "syncing",
            "Fetching IMAP message batch",
            mailbox=work.name,
            batch_size=len(uids),
            uid_start=uids[0] if uids else None,
            uid_end=uids[-1] if uids else None,
            remaining_in_mailbox=len(work.uids),
        )
        config = self.bridge.config
        max_bytes = int(config.get("max_message_bytes") or _DEFAULT_MAX_MESSAGE_BYTES)
        batch_bytes = int(config.get("max_batch_bytes") or _DEFAULT_MAX_BATCH_BYTES)
        sizes = self._with_retry(
            work.name,
            lambda: self._client_or_fail().fetch(uids, [b"RFC822.SIZE"]),
            expected_uidvalidity=work.uidvalidity,
        )
        self._report_unanswered(work.name, uids, sizes, phase="size screen")
        small = [uid for uid in uids if uid in sizes and int(sizes[uid].get(b"RFC822.SIZE", 0)) <= max_bytes]
        small_set = set(small)
        oversized = [uid for uid in uids if uid in sizes and uid not in small_set]
        messages: list[ParsedMessage] = []
        for run in _byte_budget_runs(small, sizes, batch_bytes):
            data = self._with_retry(
                work.name,
                partial(self._fetch_bodies, run),
                expected_uidvalidity=work.uidvalidity,
            )
            self._report_unanswered(work.name, run, data, phase="body fetch")
            messages.extend(self._parse_fetched(work, data, body_key=b"BODY[]"))
        if oversized:
            data = self._with_retry(
                work.name,
                lambda: self._client_or_fail().fetch(
                    oversized, [b"BODY.PEEK[HEADER]", b"FLAGS", b"INTERNALDATE", b"RFC822.SIZE"]
                ),
                expected_uidvalidity=work.uidvalidity,
            )
            self._report_unanswered(work.name, oversized, data, phase="header fetch")
            messages.extend(self._parse_fetched(work, data, body_key=b"BODY[HEADER]", truncated=True))
        return messages

    @staticmethod
    def _uidnext(mailbox: str, status: dict[bytes, Any]) -> int:
        """Require the server's next allocatable UID for safe mailbox boundaries."""

        try:
            uidnext = int(status[b"UIDNEXT"])
            if uidnext < 1:
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise ImapError(
                f"IMAP mailbox {mailbox!r} did not report a valid UIDNEXT. "
                "Check the server's IMAP STATUS support, then retry."
            ) from error
        return uidnext

    def _confirm_expunged(
        self,
        mailbox: str,
        uidvalidity: int,
        unanswered: set[int],
        *,
        retry_hint: str,
    ) -> set[int]:
        """Permit advancing only when every unanswered UID is confirmed absent."""

        still_present = self._present_uids(mailbox, uidvalidity, sorted(unanswered))
        if still_present:
            raise ImapError(
                f"IMAP mailbox {mailbox!r} still contains UID(s) that its FETCH did not answer: "
                f"{sorted(still_present)[:20]}. {retry_hint}"
            )
        return unanswered

    def _present_uids(self, mailbox: str, uidvalidity: int, uids: list[int]) -> set[int]:
        """Return unanswered UIDs that still exist in the planned mailbox epoch."""

        if not uids:
            return set()

        def search() -> set[int]:
            client = self._client_or_fail()
            current = int(client.folder_status(mailbox, [b"UIDVALIDITY"])[b"UIDVALIDITY"])
            if current != uidvalidity:
                self._invalidate_cursor(mailbox, uidvalidity=current)
            sequence = ",".join(str(uid) for uid in uids)
            return {int(uid) for uid in client.search(["UID", sequence])} & set(uids)

        return self._with_retry(mailbox, search)

    def _fetch_bodies(self, run: list[int]) -> dict[int, dict[bytes, Any]]:
        """Pull one byte-budgeted run of full bodies (flags and receipt time ride along)."""

        return self._client_or_fail().fetch(run, [b"BODY.PEEK[]", b"FLAGS", b"INTERNALDATE"])

    def _parse_fetched(
        self,
        work: _MailboxWork,
        data: dict[int, dict[bytes, Any]],
        *,
        body_key: bytes,
        truncated: bool = False,
    ) -> list[ParsedMessage]:
        """Parse one fetch response; a poison message lands via the fallback envelope."""

        messages: list[ParsedMessage] = []
        for uid in sorted(data):
            item = data[uid]
            raw = item.get(body_key)
            if raw is None:
                logger.warning("imap sync %r: UID %s answered without %s.", work.name, uid, body_key.decode())
                continue
            flags = tuple(item.get(b"FLAGS", ()))
            internal_date = item.get(b"INTERNALDATE")
            try:
                messages.append(
                    parse_message(
                        bytes(raw),
                        mailbox=work.name,
                        uid=int(uid),
                        uidvalidity=work.uidvalidity,
                        flags=flags,
                        internal_date=internal_date,
                        own_addresses=self._own_addresses,
                        truncated_bytes=int(item[b"RFC822.SIZE"]) if truncated else None,
                    )
                )
            except Exception as error:  # noqa: BLE001 — one poison message must not abort the sync.
                messages.append(
                    fallback_message(
                        bytes(raw),
                        mailbox=work.name,
                        uid=int(uid),
                        uidvalidity=work.uidvalidity,
                        flags=flags,
                        internal_date=internal_date,
                        error=error,
                    )
                )
        return messages

    @staticmethod
    def _report_unanswered(mailbox: str, requested: list[int], answered: dict[int, Any], *, phase: str) -> None:
        """Log UIDs a fetch did not answer — usually expunged, but never silent.

        Before advancing, the caller confirms missing UIDs with an exact UID
        search. Confirmed expunges may advance; a UID that still exists fails the
        sync with its cursor unchanged. This stage log identifies which FETCH
        response was incomplete when that failure is investigated.
        """

        missing = [uid for uid in requested if uid not in answered]
        if missing:
            logger.warning(
                "imap sync %r: %d UID(s) unanswered at the %s (expunged or server hiccup): %s",
                mailbox,
                len(missing),
                phase,
                missing[:20],
            )

    def _with_retry(
        self,
        mailbox: str,
        operation: Callable[[], _T],
        *,
        expected_uidvalidity: int | None = None,
    ) -> _T:
        """Run one server operation, reconnecting and retrying once on transport loss."""

        try:
            return operation()
        except _TRANSIENT_ERRORS:
            self.close()
            self._connect()
            self._select(mailbox, expected_uidvalidity=expected_uidvalidity)
            return operation()

    # --- connection ---

    def test_connection(self, *, using: str | None = None) -> str:
        """Dial, secure, log in, and log out again — the operator's connection test.

        The same path a sync takes up to its first mailbox, so a wrong host,
        a refused TLS handshake, or a rejected password surfaces here with the
        ``ImapError`` the sync would have recorded, instead of only in the
        worker log after the next poll.
        """

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        self._load_credentials(using=using)
        client = self._open()
        try:
            username = self._login(client)
        finally:
            self._client = client
            self.close()
        return f"Signed in to {self._host()} as {username}."

    def _connect(self) -> IMAPClient:
        """Open, secure, and authenticate the IMAP session from config + credential."""

        client = self._open()
        self._login_name = self._login(client)
        self._client = client
        self._selected = ""
        return client

    def _host(self) -> str:
        """Return the configured IMAP host, or raise when the channel has none."""

        host = str(self.bridge.config.get("host") or "").strip()
        if not host:
            raise ImapError("An IMAP host is required.")
        return host

    def _open(self) -> IMAPClient:
        """Dial and secure the transport from config; authentication is :meth:`_login`."""

        config = self.bridge.config
        host = self._host()
        security = str(config.get("security") or "ssl")
        if security not in ("ssl", "starttls", "plain"):
            raise ImapError(f"Unknown IMAP security mode {security!r}.")
        port = int(config["port"]) if config.get("port") else None
        self._check_host(host, port)
        context = ssl.create_default_context() if security in ("ssl", "starttls") else None
        try:
            client = self.client_class(
                host,
                port=port,
                ssl=security == "ssl",
                ssl_context=context if security == "ssl" else None,
                timeout=int(config.get("timeout") or _DEFAULT_TIMEOUT_SECONDS),
            )
            # IMAPClient's default converts INTERNALDATE to *naive local* time; the
            # parser needs the aware server-declared offset, so times stay honest on
            # any host timezone.
            client.normalise_times = False
            if security == "starttls":
                client.starttls(context)
        except _TRANSIENT_ERRORS as error:
            logger.exception("IMAP connection to %s failed.", host)
            raise ImapError(
                f"IMAP connection to {host} failed. Check the host, port, and security settings."
            ) from error
        return client

    @staticmethod
    def _check_host(host: str, port: int | None) -> None:
        """Judge the operator-supplied host through the shared outbound-address owner.

        ``integrate.net`` owns the policy; ``allow_private=True`` is its
        operator-configured-connection mode — self-hosted mail on a private
        network works, metadata/link-local escapes are still refused. The client
        then dials the hostname (TLS verification needs the name), so the
        resolve-then-connect gap stays open here, as it does for any non-HTTP
        transport without an IP-pinning layer.
        """

        try:
            addresses = resolved_addresses(host, port)
        except ValidationError as error:
            raise ImapError(f"IMAP host {host!r} could not be resolved.") from error
        for address in addresses:
            if is_unsafe_address(address, allow_private=True):
                raise ImapError(f"IMAP host {host!r} resolves to a forbidden address.")

    def _select(self, name: str, *, expected_uidvalidity: int | None = None) -> None:
        """SELECT ``name`` read-only unless it is already the session's folder.

        IMAP FETCH is stateful — it reads the *currently selected* folder, and
        discovery leaves the last-planned mailbox selected — so every chunk pins
        its own mailbox before fetching.
        """

        if self._selected == name:
            return
        status = self._client_or_fail().select_folder(name, readonly=True)
        self._selected = name
        if expected_uidvalidity is not None and int(status[b"UIDVALIDITY"]) != expected_uidvalidity:
            self._invalidate_cursor(name, uidvalidity=int(status[b"UIDVALIDITY"]))

    def _login(self, client: IMAPClient) -> str:
        """Authenticate with the channel credential (LOGIN or XOAUTH2); return the login name.

        The server's refusal becomes an ``ImapError`` naming the login and the
        host — the one line an operator needs to fix a rotated or mistyped
        password — while the password itself never leaves this frame.
        """

        credential = self._credential
        if credential is None:
            raise ImapError("An IMAP channel requires a credential.")
        if credential.kind == CredentialKind.BASIC_AUTH:
            material = credential.reveal()
            # The username stored beside the password outranks the connected
            # account email — IMAP login names and mailbox addresses differ on
            # plenty of self-hosted servers.
            username = self._configured_username() or str(material.get("username", "")) or self._account_email()
            self._authenticate(username, partial(client.login, username, str(material.get("password", ""))))
            return username
        if credential.kind == CredentialKind.OAUTH:
            credential.ensure_fresh()
            username = self._configured_username() or self._account_email()
            if not username:
                raise ImapError("An OAuth IMAP login needs a username (config or connected account email).")
            self._authenticate(username, partial(client.oauth2_login, username, credential.secret_value()))
            return username
        raise ImapError(f"IMAP cannot authenticate with a {credential.kind} credential.")

    def _authenticate(self, username: str, login: Callable[[], Any]) -> None:
        """Run one login call, translating the server's refusal to the operator message."""

        try:
            login()
        except LoginError as error:
            logger.exception("IMAP login failed for %r at %s.", username, self._host())
            raise ImapError(
                f"IMAP login failed for {username!r} at {self._host()}. Check the account credentials."
            ) from error

    def _configured_username(self) -> str:
        """Return the operator-configured login username, or ``""``."""

        return str(self.bridge.config.get("username") or "").strip()

    def _source_identity_digest(self) -> str:
        """Return a non-secret binding for the mailbox account behind this cursor.

        Password and OAuth material deliberately do not participate: rotating a
        credential for the same login keeps the boundary. ``_login`` is the one
        owner of username precedence; this method binds the exact authenticated
        name without re-deriving or normalizing it. Host/login/transport changes
        require the operator to establish a fresh boundary before sync.
        """

        if not self._login_name:
            raise RuntimeError("The IMAP source identity requires an authenticated session.")
        config = self.bridge.config
        identity = "\0".join(
            (
                self._host().casefold(),
                str(config.get("port") or ""),
                str(config.get("security") or "ssl").casefold(),
                self._login_name,
            )
        )
        return sha256(identity.encode("utf-8")).hexdigest()

    def _account_email(self) -> str:
        """Return the connected external account's email, or ``""``."""

        account = self._external_account
        if account is None:
            return ""
        return str(account.email or "").strip()

    def _resolve_own_addresses(self) -> frozenset[str]:
        """Return the account's own addresses for direction classification."""

        addresses = {str(value).strip().lower() for value in (self.bridge.config.get("own_addresses") or [])}
        credential = self._credential
        material = credential.reveal() if credential is not None else {}
        for candidate in (self._configured_username(), self._account_email(), str(material.get("username", ""))):
            cleaned = candidate.strip().lower()
            if "@" in cleaned:
                addresses.add(cleaned)
        return frozenset(address for address in addresses if address)

    def _batch_size(self) -> int:
        """Return the per-call fetch batch size (bounded below by one)."""

        return max(1, int(self.bridge.config.get("batch_size") or _DEFAULT_BATCH_SIZE))

    def _client_or_fail(self) -> IMAPClient:
        """Return the live session (reconnected by the retry wrapper when lost)."""

        if self._client is None:
            return self._connect()
        return self._client

    def close(self) -> None:
        """Log out quietly (the ChannelBackend teardown hook); the session may already be gone."""

        if self._client is None:
            return
        try:
            self._client.logout()
        except Exception:  # noqa: BLE001 — a dead connection at logout is not a sync failure.
            pass
        self._client = None
        self._selected = ""
        self._login_name = ""


def _byte_budget_runs(uids: list[int], sizes: dict[int, dict[bytes, Any]], budget: int) -> list[list[int]]:
    """Split UIDs into runs whose cumulative RFC822.SIZE stays inside one fetch budget.

    ``batch_size`` bounds the count; this bounds the bytes, so two hundred
    near-cap messages cannot buffer gigabytes inside a single FETCH response. A
    single message larger than the budget still fetches alone — the per-message
    cap is ``max_message_bytes``, screened before this.
    """

    runs: list[list[int]] = []
    current: list[int] = []
    total = 0
    for uid in uids:
        size = int(sizes[uid].get(b"RFC822.SIZE", 0))
        if current and total + size > budget:
            runs.append(current)
            current = []
            total = 0
        current.append(uid)
        total += size
    if current:
        runs.append(current)
    return runs


def _text(value: object) -> str:
    """Return an IMAP token (bytes on the wire, str from some servers) as text."""

    return value.decode("ascii", "replace") if isinstance(value, bytes) else str(value)
