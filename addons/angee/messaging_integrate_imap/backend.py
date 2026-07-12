"""IMAP channel backend: incremental mailbox sync over IMAPClient.

Transport + cursor only — parsing is :mod:`.parser`, and the idempotent map onto
threads/messages/parts is ``Message.objects.ingest``. The sync is strictly
read-only against the server: every folder is opened read-only and bodies are
fetched with ``BODY.PEEK``, so no ``\\Seen`` flag is ever set by a sync.

Incremental state lives on ``bridge.cursor`` as per-mailbox UID watermarks::

    {"mailboxes": {"INBOX": {"uidvalidity": 123456, "last_uid": 4211}}}

Correctness rests on three facts. UIDVALIDITY is checked every run: a changed
value invalidates that mailbox's UID space, so its cursor resets and the folder
refetches in full — the ``(platform, external_id)`` ingest idempotency converges
the refetch instead of duplicating it. UIDNEXT (from STATUS, no SELECT) pre-screens
each unchanged mailbox so an idle folder costs one round-trip. And the cursor
advances only in memory during a run — ``Bridge.record_sync`` persists it after
the whole run succeeds, so a crash can re-fetch but never skip mail.

``fetch_messages`` follows the seam's paging contract (one bounded batch per
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
from dataclasses import dataclass, field
from functools import partial
from typing import Any, TypeVar

from django.core.exceptions import ValidationError
from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientAbortError

from angee.integrate.credentials import CredentialKind
from angee.integrate.net import is_unsafe_address, resolved_addresses
from angee.integrate.sync import current_bridge_progress
from angee.messaging.backends import ChannelBackend, ParsedMessage
from angee.messaging_integrate_imap.parser import fallback_message, parse_message

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_TRANSIENT_ERRORS = (OSError, EOFError, IMAPClientAbortError)
_DEFAULT_BATCH_SIZE = 200
_DEFAULT_MAX_MESSAGE_BYTES = 50_000_000
_DEFAULT_MAX_BATCH_BYTES = 64_000_000
_DEFAULT_TIMEOUT_SECONDS = 60
# Folders never worth syncing by default; SPECIAL-USE flags are authoritative,
# these casefolded names are the fallback for servers that do not advertise them.
_SKIP_SPECIAL_USE = frozenset({"\\junk", "\\trash", "\\drafts"})
_SKIP_FOLDER_NAMES = frozenset({"junk", "spam", "trash", "drafts", "deleted items", "deleted messages"})


class ImapError(Exception):
    """Raised when the channel's IMAP configuration or credential is unusable."""


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


class ImapChannelBackend(ChannelBackend):
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
        self._work: deque[_MailboxWork] | None = None
        self._own_addresses: frozenset[str] = frozenset()

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return the next batch since the cursor; empty once every mailbox drained.

        The first call connects, screens each selected mailbox through its cursor,
        and builds the fetch plan; subsequent calls page through it, advancing the
        in-memory cursor past each returned chunk. A chunk whose UIDs all vanished
        server-side between search and fetch yields nothing — the loop then moves
        on rather than reporting a premature drain.
        """

        if self._work is None:
            self._work = self._discover()
        batch_size = self._batch_size()
        while self._work:
            work = self._work[0]
            chunk = work.take(batch_size)
            if not chunk:
                self._work.popleft()
                continue
            messages = self._fetch_chunk(work, chunk)
            self._advance_cursor(work.name, work.uidvalidity, chunk[-1])
            if messages:
                return messages
        self.close()
        return []

    def sync_partitions(self) -> tuple[str, ...]:
        """Return the channel's selected mailbox names — one drainable partition each.

        Each mailbox owns an independent UID watermark in the cursor, so mailboxes
        are the natural parallel unit: every partition syncs on its own backend
        instance and IMAP connection (servers commonly cap per-connection
        concurrency, not per-account). Connects once to list folders — the same
        selection :meth:`_discover` uses — and releases the connection; the
        partition drains reconnect on their own instances.
        """

        try:
            client = self._connect()
            return tuple(self._select_mailboxes(client))
        finally:
            self.close()

    def partition_cursor_slice(self, partition: str) -> tuple[tuple[str, ...], Any]:
        """Return one mailbox's cursor fragment — ``("mailboxes", name) -> watermark``."""

        cursor = self.bridge.cursor if isinstance(self.bridge.cursor, dict) else {}
        raw_mailboxes = cursor.get("mailboxes")
        mailboxes: dict[str, Any] = raw_mailboxes if isinstance(raw_mailboxes, dict) else {}
        return (("mailboxes", partition), mailboxes.get(partition))

    def _report_progress(self, stage: str, message: str, **details: Any) -> None:
        """Publish IMAP-specific progress into the generic bridge reporter."""

        reporter = current_bridge_progress()
        if reporter is None:
            return
        previous_details = {}
        if isinstance(self.bridge.sync_progress, dict):
            previous_details = dict(self.bridge.sync_progress.get("details") or {})
        previous_details.update({"backend": self.key, **details})
        reporter.report(stage, message=message, details=previous_details)

    # --- discovery ---

    def _discover(self) -> deque[_MailboxWork]:
        """Build the fetch plan: per selected mailbox, the UIDs beyond its cursor."""

        client = self._connect()
        self._own_addresses = self._resolve_own_addresses()
        mailboxes = self.bridge.cursor.setdefault("mailboxes", {})
        plan: deque[_MailboxWork] = deque()
        selected_mailboxes = self._select_mailboxes(client)
        if self.partition is not None:
            # A partition drain plans only its own mailbox; the parent already
            # enumerated the full selection through sync_partitions().
            selected_mailboxes = [name for name in selected_mailboxes if name == self.partition]
        self._report_progress(
            "discovering",
            "Discovered IMAP mailboxes",
            mailbox_count=len(selected_mailboxes),
        )
        for index, name in enumerate(selected_mailboxes, start=1):
            status = client.folder_status(name, [b"UIDVALIDITY", b"UIDNEXT"])
            uidvalidity = int(status[b"UIDVALIDITY"])
            uidnext = int(status[b"UIDNEXT"])
            entry = mailboxes.get(name) or {}
            last_uid = int(entry.get("last_uid", 0)) if int(entry.get("uidvalidity", -1)) == uidvalidity else 0
            if last_uid and uidnext <= last_uid + 1:
                continue  # nothing new — screened without a SELECT round-trip
            self._select(name)
            criteria = ["UID", f"{last_uid + 1}:*"] if last_uid else "ALL"
            # A UID range of ``n:*`` returns the highest-UID message even when its
            # UID is below ``n`` (RFC 3501), so re-filter client-side.
            uids = sorted(int(uid) for uid in client.search(criteria) if int(uid) > last_uid)
            if not uids:
                mailboxes[name] = {"uidvalidity": uidvalidity, "last_uid": last_uid}
                self._report_progress(
                    "discovering",
                    "Planned IMAP mailbox sync",
                    mailbox=name,
                    mailbox_index=index,
                    mailbox_count=len(selected_mailboxes),
                    queued_messages=0,
                    total_queued=sum(len(item.uids) for item in plan),
                )
                continue
            plan.append(_MailboxWork(name=name, uidvalidity=uidvalidity, uids=uids))
            self._report_progress(
                "discovering",
                "Planned IMAP mailbox sync",
                mailbox=name,
                mailbox_index=index,
                mailbox_count=len(selected_mailboxes),
                queued_messages=len(uids),
                total_queued=sum(len(item.uids) for item in plan),
            )
        return plan

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

        self._select(work.name)
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
        sizes = self._with_retry(work.name, lambda: self._client_or_fail().fetch(uids, [b"RFC822.SIZE"]))
        self._report_unanswered(work.name, uids, sizes, phase="size screen")
        small = [uid for uid in uids if uid in sizes and int(sizes[uid].get(b"RFC822.SIZE", 0)) <= max_bytes]
        small_set = set(small)
        oversized = [uid for uid in uids if uid in sizes and uid not in small_set]
        messages: list[ParsedMessage] = []
        for run in _byte_budget_runs(small, sizes, batch_bytes):
            data = self._with_retry(work.name, partial(self._fetch_bodies, run))
            self._report_unanswered(work.name, run, data, phase="body fetch")
            messages.extend(self._parse_fetched(work, data, body_key=b"BODY[]"))
        if oversized:
            data = self._with_retry(
                work.name,
                lambda: self._client_or_fail().fetch(
                    oversized, [b"BODY.PEEK[HEADER]", b"FLAGS", b"INTERNALDATE", b"RFC822.SIZE"]
                ),
            )
            self._report_unanswered(work.name, oversized, data, phase="header fetch")
            messages.extend(self._parse_fetched(work, data, body_key=b"BODY[HEADER]", truncated=True))
        return messages

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

        The cursor still advances past them (a vanished message has nothing left
        to fetch), so the log line is the only trace distinguishing an expunge
        from a misbehaving server; a recurring pattern here is the signal to
        investigate before trusting the watermark.
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

    def _with_retry(self, mailbox: str, operation: Callable[[], _T]) -> _T:
        """Run one server operation, reconnecting and retrying once on transport loss."""

        try:
            return operation()
        except _TRANSIENT_ERRORS:
            self.close()
            self._connect()
            self._select(mailbox)
            return operation()

    # --- cursor ---

    def _advance_cursor(self, name: str, uidvalidity: int, last_uid: int) -> None:
        """Move the in-memory watermark past a fetched chunk (UIDs page in ascending order)."""

        self.bridge.cursor.setdefault("mailboxes", {})[name] = {
            "uidvalidity": uidvalidity,
            "last_uid": last_uid,
        }

    # --- connection ---

    def _connect(self) -> IMAPClient:
        """Open, secure, and authenticate the IMAP session from config + credential."""

        config = self.bridge.config
        host = str(config.get("host") or "").strip()
        if not host:
            raise ImapError("An IMAP host is required.")
        security = str(config.get("security") or "ssl")
        if security not in ("ssl", "starttls", "plain"):
            raise ImapError(f"Unknown IMAP security mode {security!r}.")
        port = int(config["port"]) if config.get("port") else None
        self._check_host(host, port)
        context = ssl.create_default_context() if security in ("ssl", "starttls") else None
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
        self._login(client)
        self._client = client
        self._selected = ""
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

    def _select(self, name: str) -> None:
        """SELECT ``name`` read-only unless it is already the session's folder.

        IMAP FETCH is stateful — it reads the *currently selected* folder, and
        discovery leaves the last-planned mailbox selected — so every chunk pins
        its own mailbox before fetching.
        """

        if self._selected == name:
            return
        self._client_or_fail().select_folder(name, readonly=True)
        self._selected = name

    def _login(self, client: IMAPClient) -> None:
        """Authenticate with the channel credential (LOGIN or XOAUTH2)."""

        credential = self.bridge.credential
        if credential is None:
            raise ImapError("An IMAP channel requires a credential.")
        if credential.kind == CredentialKind.BASIC_AUTH:
            material = credential.reveal()
            # The username stored beside the password outranks the connected
            # account email — IMAP login names and mailbox addresses differ on
            # plenty of self-hosted servers.
            username = self._configured_username() or str(material.get("username", "")) or self._account_email()
            client.login(username, str(material.get("password", "")))
            return
        if credential.kind == CredentialKind.OAUTH:
            credential.ensure_fresh()
            username = self._configured_username() or self._account_email()
            if not username:
                raise ImapError("An OAuth IMAP login needs a username (config or connected account email).")
            client.oauth2_login(username, credential.secret_value())
            return
        raise ImapError(f"IMAP cannot authenticate with a {credential.kind} credential.")

    def _configured_username(self) -> str:
        """Return the operator-configured login username, or ``""``."""

        return str(self.bridge.config.get("username") or "").strip()

    def _account_email(self) -> str:
        """Return the connected external account's email, or ``""``."""

        credential = self.bridge.credential
        account = credential.external_account if credential is not None else None
        if account is None:
            return ""
        return str(account.email or "").strip()

    def _resolve_own_addresses(self) -> frozenset[str]:
        """Return the account's own addresses for direction classification."""

        addresses = {str(value).strip().lower() for value in (self.bridge.config.get("own_addresses") or [])}
        credential = self.bridge.credential
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
