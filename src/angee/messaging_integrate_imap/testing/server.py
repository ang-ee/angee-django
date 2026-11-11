"""An in-memory IMAP account for tests of the IMAP channel backend."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import partial
from typing import Any
from unittest.mock import patch

import pytest

from angee.messaging_integrate_imap.backend import ImapChannelBackend

INTERNAL_DATE = datetime(2026, 7, 2, 9, 30, tzinfo=UTC)
"""The INTERNALDATE a message reports unless its entry sets ``internal_date``."""


def mailbox(*raws: bytes, uidvalidity: int = 100, flags: tuple[bytes, ...] = (b"\\HasNoChildren",)) -> dict[str, Any]:
    """Return one mailbox holding ``raws`` at ascending UIDs from one."""

    return {
        "flags": flags,
        "uidvalidity": uidvalidity,
        "messages": {index + 1: {"raw": raw} for index, raw in enumerate(raws)},
    }


class ImapServer:
    """One IMAP account in memory, which ``serve`` makes every IMAP channel backend dial.

    ``folders`` maps each mailbox name to a ``mailbox()`` dict: its LIST ``flags``,
    ``uidvalidity`` and ``messages`` (UID to ``raw`` bytes with optional ``flags`` and
    ``internal_date``). UIDNEXT never falls back below an expunged UID, as on a real
    server. Every connection, login, STATUS, SELECT, SEARCH, FETCH, LOGOUT and
    shutdown is recorded. Server-side events are scripted rather than patched:
    ``withheld`` UIDs stay present but FETCH leaves them unanswered,
    ``reports_uidnext`` drops UIDNEXT from STATUS, and ``before``/``after`` hooks run
    with a command's arguments around every call of it (``connect``, ``login``,
    ``oauth2_login``, ``folder_status``, ``search``, ``fetch``, ``logout``): raise to
    refuse it, or expunge, deliver and change epochs.
    """

    def __init__(self, folders: dict[str, dict[str, Any]]) -> None:
        """Serve ``folders``; nothing is recorded until a client dials."""

        self.folders = folders
        self.withheld: set[int] = set()
        self.reports_uidnext = True
        self.before: dict[str, Callable[..., object]] = {}
        self.after: dict[str, Callable[..., object]] = {}
        self.connections: list[dict[str, Any]] = []
        self.logins: list[tuple[str, str, str]] = []
        self.status_calls: list[str] = []
        self.selects: list[str] = []
        self.searches: list[tuple[str, Any]] = []
        self.fetches: list[tuple[str, tuple[int, ...], tuple[bytes, ...]]] = []
        self.read_timeouts: list[float | None] = []
        self.logouts = 0
        self.shutdowns = 0

    def uids(self, name: str) -> list[int]:
        """Return the UIDs present in mailbox ``name``, ascending."""

        return sorted(self.folders[name].get("messages", {}))

    def uidnext(self, name: str) -> int:
        """Return the next UID mailbox ``name`` will allocate."""

        return max(max(self.uids(name), default=0) + 1, int(self.folders[name].get("uidnext", 1)))

    def deliver(self, name: str, raw: bytes, **fields: Any) -> int:
        """Append one message to mailbox ``name`` at UIDNEXT and return its UID."""

        uid = self.uidnext(name)
        self.folders[name].setdefault("messages", {})[uid] = {"raw": raw, **fields}
        return uid

    def expunge(self, name: str, *uids: int) -> None:
        """Remove ``uids`` from mailbox ``name``; their UIDs are never reused."""

        folder = self.folders[name]
        folder["uidnext"] = self.uidnext(name)
        for uid in uids:
            folder.get("messages", {}).pop(uid, None)

    def refuse(self, command: str, error: BaseException) -> None:
        """Fail every later ``command`` with ``error`` before the server answers it."""

        def raise_error(*args: Any) -> None:
            raise error

        self.before[command] = raise_error

    def command(self, name: str, answer: Callable[..., Any], *args: Any) -> Any:
        """Answer one command between its scripted hooks."""

        if (before := self.before.get(name)) is not None:
            before(*args)
        result = answer(*args)
        if (after := self.after.get(name)) is not None:
            after(*args)
        return result


class ImapClient:
    """The ``imapclient.IMAPClient`` surface the backend drives, answering from one ``ImapServer``."""

    def __init__(
        self,
        server: ImapServer,
        host: str,
        *,
        port: int | None = None,
        ssl: bool = True,
        ssl_context: Any = None,
        timeout: float | None = None,
    ) -> None:
        """Dial ``server``; the ``connect`` hooks see the dial arguments."""

        dial = {"host": host, "port": port, "ssl": ssl, "ssl_context": ssl_context, "timeout": timeout}
        server.command("connect", lambda: server.connections.append(dial))
        self.server = server
        self.selected = ""
        # IMAPClient converts INTERNALDATE to naive local time by default; FETCH
        # refuses to answer until the backend opts out, as its parser needs offsets.
        self.normalise_times = True

    def login(self, username: str, password: str) -> None:
        self.server.command("login", lambda *_: self.server.logins.append(("login", username, password)), username)

    def oauth2_login(self, user: str, access_token: str) -> None:
        self.server.command("oauth2_login", lambda *_: self.server.logins.append(("oauth2", user, access_token)), user)

    def starttls(self, ssl_context: Any = None) -> None:
        del ssl_context

    def list_folders(self) -> list[tuple[tuple[bytes, ...], bytes, str]]:
        folders = self.server.folders.items()
        return [(folder.get("flags", (b"\\HasNoChildren",)), b"/", name) for name, folder in folders]

    def folder_status(self, name: str, what: Any = None) -> dict[bytes, int]:
        del what
        return self.server.command("folder_status", self._status, name)

    def _status(self, name: str) -> dict[bytes, int]:
        self.server.status_calls.append(name)
        status = {b"UIDVALIDITY": self.server.folders[name]["uidvalidity"]}
        if self.server.reports_uidnext:
            status[b"UIDNEXT"] = self.server.uidnext(name)
        return status

    def select_folder(self, name: str, readonly: bool = False) -> dict[bytes, int]:
        assert readonly, "the sync must never open a folder read-write"
        self.selected = name
        self.server.selects.append(name)
        return {b"UIDVALIDITY": self.server.folders[name]["uidvalidity"]}

    def search(self, criteria: Any = "ALL") -> list[int]:
        return self.server.command("search", self._search, criteria)

    def _search(self, criteria: Any) -> list[int]:
        self.server.searches.append((self.selected, criteria))
        uids = self.server.uids(self.selected)
        if not isinstance(criteria, (list, tuple)):
            return uids
        messages = self.server.folders[self.selected]["messages"]
        if "UID" in criteria:
            sequence = str(criteria[criteria.index("UID") + 1])
            if ":" in sequence:
                start, end = sequence.split(":", 1)
                lower, upper = sorted((int(start), max(uids, default=0) if end == "*" else int(end)))
                uids = [uid for uid in uids if lower <= uid <= upper]
            else:
                requested = {int(value) for value in sequence.split(",")}
                uids = [uid for uid in uids if uid in requested]
        if "SINCE" in criteria:
            since = criteria[criteria.index("SINCE") + 1]
            uids = [uid for uid in uids if messages[uid].get("internal_date", INTERNAL_DATE).date() >= since]
        if "BEFORE" in criteria:
            before = criteria[criteria.index("BEFORE") + 1]
            uids = [uid for uid in uids if messages[uid].get("internal_date", INTERNAL_DATE).date() < before]
        return uids

    def fetch(self, uids: list[int], data: list[bytes]) -> dict[int, dict[bytes, Any]]:
        return self.server.command("fetch", self._fetch, uids, data)

    def _fetch(self, uids: list[int], data: list[bytes]) -> dict[int, dict[bytes, Any]]:
        assert self.normalise_times is False, "the sync must disable naive-local INTERNALDATE normalisation"
        self.server.fetches.append((self.selected, tuple(uids), tuple(data)))
        messages = self.server.folders[self.selected].get("messages", {})
        response: dict[int, dict[bytes, Any]] = {}
        for uid in uids:
            entry = messages.get(uid)
            if entry is None or uid in self.server.withheld:
                continue
            raw = entry["raw"]
            answers = {
                b"RFC822.SIZE": (b"RFC822.SIZE", len(raw)),
                b"FLAGS": (b"FLAGS", entry.get("flags", (b"\\Seen",))),
                b"INTERNALDATE": (b"INTERNALDATE", entry.get("internal_date", INTERNAL_DATE)),
                b"BODY.PEEK[]": (b"BODY[]", raw),
                b"BODY.PEEK[HEADER]": (b"BODY[HEADER]", raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"),
            }
            response[uid] = dict(answers[key] for key in data if key in answers)
        return response

    def logout(self) -> None:
        self.server.command("logout", self._logout)

    def _logout(self) -> None:
        self.server.logouts += 1

    def socket(self) -> ImapClient:
        """Expose the native timeout surface without a network socket."""

        return self

    def settimeout(self, timeout: float | None) -> None:
        self.server.read_timeouts.append(timeout)

    def shutdown(self) -> None:
        self.server.shutdowns += 1


@contextmanager
def serve(server: ImapServer) -> Iterator[ImapServer]:
    """Dial ``server`` from every IMAP channel backend built inside the block."""

    with patch.object(ImapChannelBackend, "client_class", staticmethod(partial(ImapClient, server))):
        yield server


@pytest.fixture
def imap_server() -> Iterator[ImapServer]:
    """An IMAP account, empty until the test adds mailboxes, that every backend built in the test dials."""

    with serve(ImapServer({})) as server:
        yield server
