"""Slack channel backend: serial, bounded polling over ``slack_sdk``.

The bridge stores a timestamp watermark and any in-progress history page cursor
per Slack conversation, plus a bounded per-thread reply watermark::

    {
        "conversations": {
            "C123": {
                "last_ts": "1784700000.000100",
                "history": {
                    "cursor": "next-page",
                    "oldest": "1784700000.000100",
                    "last_ts": "1784700010.000100",
                },
            }
        },
        "threads": {"C123": {"1784700000.000100": "1784700005.000100"}},
    }

``conversations.history`` is newest-first, so a page cursor is persisted before
the conversation watermark advances: a crash may replay an ingested page but can
never skip an older one. ``conversations.replies`` is earliest-first, allowing
each active thread's watermark to advance after every bounded slice. Thread
parents whose parent/latest-reply timestamp falls outside ``backfill_days`` are
pruned, which bounds the independent late-reply rescan.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from typing import Any, ClassVar, TypeVar

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from angee.messaging.backends import ChannelBackend, MediaItem, ParsedMessage
from angee.messaging_integrate_slack.identity import parsed_message, response_data

_T = TypeVar("_T")

_CONVERSATION_TYPES = "public_channel,private_channel,mpim,im"
_PAGE_LIMIT = 200
_DEFAULT_BATCH_SIZE = 200
_DEFAULT_BACKFILL_DAYS = 90
_DEFAULT_MAX_MEDIA_BYTES = 50_000_000
_DEFAULT_MAX_BATCH_BYTES = 64_000_000
_MAX_RATE_LIMIT_RETRIES = 5
_MAX_RATE_LIMIT_SLEEP_SECONDS = 60.0


class SlackRateLimitError(TimeoutError):
    """Slack kept this poll rate-limited beyond its bounded retry/time budget."""


@dataclass
class _HistoryPage:
    """One fetched history page retained until every raw item is consumed."""

    messages: deque[dict[str, Any]]
    next_cursor: str
    last_ts: str
    oldest: str = ""


@dataclass
class _ReplyWork:
    """One active thread's earliest-first reply pagination state."""

    parent_ts: str
    oldest: str
    cursor: str = ""
    page: _HistoryPage | None = None


@dataclass
class _ConversationWork:
    """One conversation's history scan followed by its active-thread rescans."""

    conversation: dict[str, Any]
    history_done: bool = False
    history_page: _HistoryPage | None = None
    replies: deque[_ReplyWork] = field(default_factory=deque)
    reply_ids: set[str] = field(default_factory=set)


class SlackChannelBackend(ChannelBackend):
    """Poll one Slack workspace through a user-scoped internal app token.

    Slack deliberately opts out of the generic partition pool. Discovering one
    Slack conversation is not an independent cheap operation like listing IMAP
    mailboxes: every fresh partition backend would paginate the whole workspace
    conversation list and user list again, turning N conversations into O(N²)
    API work. Slack also applies one shared HTTP-429 budget to the installation,
    so parallel callers do not buy throughput. One serial backend discovers once
    and reuses its conversation plan, user cache, and rate-limit budget.

    ``config`` accepts ``backfill_days``, ``batch_size``, ``max_batch_bytes``,
    ``max_media_bytes``, and ``media_timeout_seconds``.
    """

    key = "slack"
    label = "Slack"
    icon = "message-square"
    defaults = {"vendor": "slack"}
    quote_edges: ClassVar[bool] = False

    client_class: ClassVar[type[WebClient]] = WebClient
    """Official protocol client factory; tests substitute an in-memory client."""

    def __init__(self, integration: object) -> None:
        """Bind the channel and initialize this run's paging/user caches."""

        super().__init__(integration)
        self._client: WebClient | None = None
        self._work: deque[_ConversationWork] | None = None
        self._users: dict[str, dict[str, Any]] | None = None

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return one bounded history/reply slice; empty once all work is drained.

        History page cursors and final timestamps advance only after every raw
        item in the page has been consumed. Reply timestamps advance item by item
        because Slack returns replies earliest-first. The generic channel drain
        persists these cursor changes after each non-empty returned slice; a
        successful empty completion is persisted by ``Bridge.record_sync``.
        """

        if self._work is None:
            self._work = deque(self._conversation_work(item) for item in self._discover_conversations())
        while self._work:
            work = self._work[0]
            if not work.history_done:
                batch = self._history_batch(work)
                if batch:
                    return batch
                if not work.history_done:
                    continue
            batch = self._reply_batch(work)
            if batch:
                return batch
            if work.replies:
                continue
            self._work.popleft()
        return []

    def sync_partitions(self) -> tuple[str, ...]:
        """Keep Slack on the single-instance serial drain documented by this class."""

        return ()

    def _conversation_work(self, conversation: dict[str, Any]) -> _ConversationWork:
        """Build one conversation plan, including its bounded active-thread set."""

        work = _ConversationWork(conversation=conversation)
        channel_id = str(conversation["id"])
        threads = self._active_threads(channel_id)
        for parent_ts in sorted(threads, key=_timestamp_text_key):
            self._queue_reply(work, parent_ts, threads[parent_ts])
        return work

    def _discover_conversations(self) -> list[dict[str, Any]]:
        """List all visible conversations once for this serial backend instance."""

        conversations: list[dict[str, Any]] = []
        cursor = ""
        while True:
            response = self._api_call(
                self._client_or_create().users_conversations,
                user=self._own_id(),
                types=_CONVERSATION_TYPES,
                exclude_archived=True,
                limit=_PAGE_LIMIT,
                cursor=cursor or None,
            )
            data = response_data(response)
            for raw in data.get("channels") or ():
                if isinstance(raw, Mapping) and raw.get("id"):
                    conversations.append(dict(raw))
            cursor = _next_cursor(data)
            if not cursor:
                break
        return conversations

    def _history_batch(self, work: _ConversationWork) -> list[ParsedMessage]:
        """Consume at most one configured slice from the current history page."""

        if work.history_page is None:
            work.history_page = self._history_page(work)
            if not work.history_page.messages:
                self._finish_history_page(work)
                return []
        page = work.history_page
        batch: list[ParsedMessage] = []
        used_bytes = 0
        while page.messages and len(batch) < self._batch_size():
            raw = page.messages[0]
            message = self._parse_without_media(raw, work.conversation)
            if (
                message is not None
                and batch
                and self._estimated_media_bytes(raw) > self._max_batch_bytes() - used_bytes
            ):
                break
            page.messages.popleft()
            self._remember_thread(work, raw)
            if message is None:
                continue
            media, downloaded = self._media(raw, byte_budget=max(0, self._max_batch_bytes() - used_bytes))
            batch.append(message.with_media(media))
            used_bytes += downloaded
        if not page.messages:
            self._finish_history_page(work)
        return batch

    def _history_page(self, work: _ConversationWork) -> _HistoryPage:
        """Fetch one newest-first history page from its durable resume point."""

        channel_id = str(work.conversation["id"])
        entry = self._conversation_cursor(channel_id)
        scan = entry.get("history")
        scan_values: Mapping[str, Any] = scan if isinstance(scan, Mapping) else {}
        cursor = str(scan_values.get("cursor") or "")
        last_ts = str(scan_values.get("last_ts") or "")
        oldest = str(scan_values.get("oldest") or self._oldest(channel_id))
        try:
            data = self._history_response(channel_id, oldest=oldest, cursor=cursor)
        except SlackApiError as error:
            if not cursor or str(response_data(error.response).get("error") or "") != "invalid_cursor":
                raise
            # Slack cursors can expire. Restarting from the saved timestamp only
            # replays idempotent pages; retaining the bad cursor would deadlock the
            # conversation forever.
            entry.pop("history", None)
            cursor = ""
            last_ts = ""
            oldest = self._oldest(channel_id)
            data = self._history_response(channel_id, oldest=oldest, cursor="")
        messages = [dict(raw) for raw in data.get("messages") or () if isinstance(raw, Mapping)]
        target = _latest_timestamp(messages, floor=last_ts)
        return _HistoryPage(
            messages=deque(sorted(messages, key=_timestamp_key)),
            next_cursor=_next_cursor(data),
            last_ts=target,
            oldest=oldest,
        )

    def _history_response(self, channel_id: str, *, oldest: str, cursor: str) -> Mapping[str, Any]:
        """Call one bounded Slack history page."""

        response = self._api_call(
            self._client_or_create().conversations_history,
            channel=channel_id,
            oldest=oldest,
            inclusive=False,
            limit=min(_PAGE_LIMIT, self._batch_size()),
            cursor=cursor or None,
        )
        return response_data(response)

    def _finish_history_page(self, work: _ConversationWork) -> None:
        """Persist the next page cursor, or commit the completed snapshot watermark."""

        page = work.history_page
        if page is None:
            return
        channel_id = str(work.conversation["id"])
        entry = self._conversation_cursor(channel_id)
        if page.next_cursor:
            entry["history"] = {"cursor": page.next_cursor, "oldest": page.oldest, "last_ts": page.last_ts}
        else:
            if page.last_ts:
                entry["last_ts"] = page.last_ts
            entry.pop("history", None)
            work.history_done = True
        work.history_page = None

    def _reply_batch(self, conversation: _ConversationWork) -> list[ParsedMessage]:
        """Return one earliest-first slice from an active thread reply rescan."""

        channel_id = str(conversation.conversation["id"])
        while conversation.replies:
            work = conversation.replies[0]
            if work.page is None:
                response = self._api_call(
                    self._client_or_create().conversations_replies,
                    channel=channel_id,
                    ts=work.parent_ts,
                    oldest=work.oldest,
                    inclusive=False,
                    limit=min(_PAGE_LIMIT, self._batch_size() + 1),
                    cursor=work.cursor or None,
                )
                data = response_data(response)
                messages = [dict(raw) for raw in data.get("messages") or () if isinstance(raw, Mapping)]
                work.page = _HistoryPage(
                    messages=deque(sorted(messages, key=_timestamp_key)),
                    next_cursor=_next_cursor(data),
                    last_ts="",
                )
            page = work.page
            batch: list[ParsedMessage] = []
            used_bytes = 0
            while page.messages and len(batch) < self._batch_size():
                raw = page.messages[0]
                timestamp = _timestamp(raw)
                if (
                    timestamp == work.parent_ts
                    or _timestamp_text_key(timestamp) <= _timestamp_text_key(work.oldest)
                    or not _reply_to(raw, work.parent_ts)
                ):
                    page.messages.popleft()
                    continue
                message = self._parse_without_media(raw, conversation.conversation)
                if (
                    message is not None
                    and batch
                    and self._estimated_media_bytes(raw) > self._max_batch_bytes() - used_bytes
                ):
                    break
                page.messages.popleft()
                self._advance_thread_cursor(channel_id, work.parent_ts, timestamp)
                if message is None:
                    continue
                media, downloaded = self._media(raw, byte_budget=max(0, self._max_batch_bytes() - used_bytes))
                batch.append(message.with_media(media))
                used_bytes += downloaded
            if not page.messages:
                work.cursor = page.next_cursor
                work.page = None
                if not work.cursor:
                    conversation.replies.popleft()
            if batch:
                return batch
        return []

    def _remember_thread(self, work: _ConversationWork, raw: Mapping[str, Any]) -> None:
        """Retain a newly observed active parent for independent future rescans."""

        if not _thread_parent(raw):
            return
        parent_ts = _timestamp(raw)
        channel_id = str(work.conversation["id"])
        threads = self._cursor_threads(channel_id)
        oldest = _thread_watermark(threads.get(parent_ts), parent_ts)
        threads[parent_ts] = oldest
        self._queue_reply(work, parent_ts, oldest)

    @staticmethod
    def _queue_reply(work: _ConversationWork, parent_ts: str, oldest: str) -> None:
        """Queue one thread once for this poll instance."""

        if parent_ts in work.reply_ids:
            return
        work.reply_ids.add(parent_ts)
        work.replies.append(_ReplyWork(parent_ts=parent_ts, oldest=oldest))

    def _active_threads(self, channel_id: str) -> dict[str, str]:
        """Prune and return thread parents active inside the configured backfill window."""

        cursor = self.bridge.cursor if isinstance(self.bridge.cursor, dict) else {}
        raw_threads = cursor.get("threads")
        if not isinstance(raw_threads, dict):
            return {}
        threads = raw_threads.get(channel_id)
        if not isinstance(threads, dict):
            return {}
        floor = self._backfill_floor()
        for parent_ts, raw_watermark in tuple(threads.items()):
            watermark = _thread_watermark(raw_watermark, parent_ts)
            if max(_timestamp_text_key(parent_ts), _timestamp_text_key(watermark)) < _timestamp_text_key(floor):
                del threads[parent_ts]
            else:
                threads[parent_ts] = watermark
        return threads

    def _advance_thread_cursor(self, channel_id: str, parent_ts: str, timestamp: str) -> None:
        """Advance one earliest-first thread reply watermark."""

        threads = self._cursor_threads(channel_id)
        current = _thread_watermark(threads.get(parent_ts), parent_ts)
        if _timestamp_text_key(timestamp) > _timestamp_text_key(current):
            threads[parent_ts] = timestamp

    def _parse_without_media(
        self,
        raw: Mapping[str, Any],
        conversation: Mapping[str, Any],
    ) -> ParsedMessage | None:
        """Reject transport noise before resolving any attachment bytes."""

        return parsed_message(
            raw,
            conversation=conversation,
            team_id=self._team_id(),
            own_id=self._own_id(),
            users=self._user_cache(),
        )

    def _user_cache(self) -> dict[str, dict[str, Any]]:
        """Return this serial backend instance's paginated ``users.list`` cache."""

        if self._users is not None:
            return self._users
        users: dict[str, dict[str, Any]] = {}
        cursor = ""
        while True:
            response = self._api_call(
                self._client_or_create().users_list,
                limit=_PAGE_LIMIT,
                cursor=cursor or None,
            )
            data = response_data(response)
            for raw in data.get("members") or ():
                if isinstance(raw, Mapping) and raw.get("id"):
                    users[str(raw["id"])] = dict(raw)
            cursor = _next_cursor(data)
            if not cursor:
                break
        self._users = users
        return users

    def _estimated_media_bytes(self, raw_message: Mapping[str, Any]) -> int:
        """Estimate downloadable bytes so a later message can move to the next slice."""

        total = 0
        for raw in _files(raw_message):
            try:
                declared = int(raw.get("size") or 0)
            except TypeError, ValueError:
                declared = 0
            total += declared if declared > 0 else self._max_media_bytes()
        return total

    def _media(self, raw_message: Mapping[str, Any], *, byte_budget: int) -> tuple[tuple[MediaItem, ...], int]:
        """Resolve surviving-message files within the per-file and batch byte caps."""

        items: list[MediaItem] = []
        downloaded = 0
        for raw in _files(raw_message):
            remaining = max(0, byte_budget - downloaded)
            content = self._download_file(raw, cap=min(self._max_media_bytes(), remaining)) if remaining else None
            items.append(
                MediaItem(
                    mime=str(raw.get("mimetype") or "application/octet-stream"),
                    name=str(raw.get("name") or raw.get("title") or ""),
                    content=content,
                )
            )
            downloaded += len(content) if content is not None else 0
        return tuple(items), downloaded

    def _download_file(self, file: Mapping[str, Any], *, cap: int | None = None) -> bytes | None:
        """Stream one private Slack file, reading no more than the configured cap plus one chunk."""

        url = str(file.get("url_private") or "").strip()
        limit = self._max_media_bytes() if cap is None else max(0, min(self._max_media_bytes(), cap))
        try:
            declared_size = int(file.get("size") or 0)
        except TypeError, ValueError:
            declared_size = 0
        if not url or limit <= 0 or declared_size > limit:
            return None
        try:
            timeout = max(1, int(self._config().get("media_timeout_seconds") or 10))
            return self.http.download_capped(
                url,
                cap=limit,
                headers={"Authorization": f"Bearer {self._token()}"},
                follow_redirects=True,
                timeout=timeout,
            )
        except Exception:  # noqa: BLE001 — a failed attachment stays visible as a marker.
            return None

    def _oldest(self, channel_id: str) -> str:
        """Return the saved watermark, or the configured bounded-backfill floor."""

        entry = self._cursor_conversations().get(channel_id)
        if isinstance(entry, Mapping) and entry.get("last_ts"):
            return str(entry["last_ts"])
        return self._backfill_floor()

    def _backfill_floor(self) -> str:
        """Return the configured time floor shared by initial history and thread retention."""

        configured_days = self._config().get("backfill_days")
        days = max(0, int(_DEFAULT_BACKFILL_DAYS if configured_days is None else configured_days))
        return f"{(datetime.now(tz=UTC) - timedelta(days=days)).timestamp():.6f}"

    def _batch_size(self) -> int:
        """Return the bounded number of parsed messages emitted per call."""

        return max(1, min(_PAGE_LIMIT, int(self._config().get("batch_size") or _DEFAULT_BATCH_SIZE)))

    def _max_media_bytes(self) -> int:
        """Return the per-file download cap."""

        return max(1, int(self._config().get("max_media_bytes") or _DEFAULT_MAX_MEDIA_BYTES))

    def _max_batch_bytes(self) -> int:
        """Return the aggregate downloaded-media cap for one returned slice."""

        return max(1, int(self._config().get("max_batch_bytes") or _DEFAULT_MAX_BATCH_BYTES))

    def _conversation_cursor(self, channel_id: str) -> dict[str, Any]:
        """Return one mutable conversation cursor entry."""

        conversations = self._cursor_conversations()
        entry = conversations.get(channel_id)
        if not isinstance(entry, dict):
            entry = {}
            conversations[channel_id] = entry
        return entry

    def _cursor_conversations(self) -> dict[str, Any]:
        """Return the mutable conversation cursor map, repairing malformed bridge state."""

        cursor = self._cursor_root()
        raw = cursor.setdefault("conversations", {})
        if not isinstance(raw, dict):
            raw = {}
            cursor["conversations"] = raw
        return raw

    def _cursor_threads(self, channel_id: str) -> dict[str, Any]:
        """Return one conversation's mutable thread-reply watermark map."""

        cursor = self._cursor_root()
        raw_threads = cursor.setdefault("threads", {})
        if not isinstance(raw_threads, dict):
            raw_threads = {}
            cursor["threads"] = raw_threads
        raw_channel = raw_threads.get(channel_id)
        if not isinstance(raw_channel, dict):
            raw_channel = {}
            raw_threads[channel_id] = raw_channel
        return raw_channel

    def _cursor_root(self) -> dict[str, Any]:
        """Return the mutable cursor root, repairing a malformed bridge value."""

        if not isinstance(self.bridge.cursor, dict):
            self.bridge.cursor = {}
        return self.bridge.cursor

    def _client_or_create(self) -> WebClient:
        """Return the token-authenticated official Slack client."""

        if self._client is None:
            self._client = self.client_class(token=self._token())
        return self._client

    def _token(self) -> str:
        """Return the channel's static user OAuth token."""

        credential = self.bridge.credential
        if credential is None:
            raise ValueError("A Slack channel requires a credential.")
        token = str(credential.secret_value()).strip()
        if not token:
            raise ValueError("A Slack channel requires a user OAuth token.")
        return token

    def _team_id(self) -> str:
        """Return the workspace id persisted by the successful connect probe."""

        return str(self._subscription_state().get("team_id") or "")

    def _own_id(self) -> str:
        """Return the authenticated Slack user id persisted by the connect probe."""

        return str(self._subscription_state().get("own_id") or "")

    def _subscription_state(self) -> dict[str, Any]:
        state = self.bridge.subscription_state
        return state if isinstance(state, dict) else {}

    def _config(self) -> dict[str, Any]:
        config = self.bridge.config
        return config if isinstance(config, dict) else {}

    def _api_call(self, operation: Callable[..., _T], **kwargs: Any) -> _T:
        """Call Slack with bounded, deadline-aware HTTP-429 retries."""

        retries = 0
        while True:
            try:
                return operation(**kwargs)
            except SlackApiError as error:
                response = error.response
                if int(getattr(response, "status_code", 0) or 0) != 429:
                    raise
                retries += 1
                if retries > _MAX_RATE_LIMIT_RETRIES:
                    raise SlackRateLimitError(
                        "Slack rate-limit retry budget exhausted; resume on the next poll."
                    ) from error
                headers = getattr(response, "headers", {}) or {}
                raw_delay = headers.get("Retry-After") or headers.get("retry-after") or "1"
                try:
                    delay = min(_MAX_RATE_LIMIT_SLEEP_SECONDS, max(0.0, float(raw_delay)))
                except TypeError, ValueError:
                    delay = 1.0
                if self.sync_deadline is not None and delay >= self.sync_deadline - monotonic():
                    raise SlackRateLimitError(
                        "Slack sync time budget exhausted while rate limited; resume next poll."
                    ) from error
                sleep(delay)
                if self.sync_deadline is not None and monotonic() >= self.sync_deadline:
                    raise SlackRateLimitError(
                        "Slack sync time budget exhausted while rate limited; resume next poll."
                    ) from error


def _next_cursor(data: Mapping[str, Any]) -> str:
    metadata = data.get("response_metadata")
    return str(metadata.get("next_cursor") or "") if isinstance(metadata, Mapping) else ""


def _files(raw: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return current-message file mappings; event unwrap is reserved for a future live seam."""

    current = _current_message(raw)
    return tuple(item for item in current.get("files") or () if isinstance(item, Mapping))


def _current_message(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Unwrap a future live-layer ``message_changed`` event; polling returns current messages."""

    if str(raw.get("subtype") or "") == "message_changed" and isinstance(raw.get("message"), Mapping):
        return raw["message"]
    return raw


def _timestamp(raw: Mapping[str, Any]) -> str:
    return str(_current_message(raw).get("ts") or raw.get("ts") or "")


def _timestamp_text_key(timestamp: str) -> tuple[int, int, str]:
    seconds, separator, fraction = timestamp.partition(".")
    try:
        return int(seconds), int(fraction if separator else 0), timestamp
    except ValueError:
        return 0, 0, timestamp


def _timestamp_key(raw: Mapping[str, Any]) -> tuple[int, int, str]:
    return _timestamp_text_key(_timestamp(raw))


def _latest_timestamp(messages: list[dict[str, Any]], *, floor: str = "") -> str:
    """Return the greatest message timestamp without moving below ``floor``."""

    timestamps = [timestamp for message in messages if (timestamp := _timestamp(message))]
    return max((floor, *timestamps), key=_timestamp_text_key) if floor or timestamps else ""


def _thread_parent(raw: Mapping[str, Any]) -> bool:
    timestamp = _timestamp(raw)
    try:
        reply_count = int(raw.get("reply_count") or 0)
    except TypeError, ValueError:
        reply_count = 0
    thread_ts = str(raw.get("thread_ts") or "")
    return bool(timestamp and (not thread_ts or thread_ts == timestamp) and reply_count > 0)


def _reply_to(raw: Mapping[str, Any], parent_ts: str) -> bool:
    return str(_current_message(raw).get("thread_ts") or "") == parent_ts


def _thread_watermark(raw: Any, parent_ts: str) -> str:
    """Read the public string cursor shape, accepting the abandoned draft mapping shape."""

    if isinstance(raw, Mapping):
        raw = raw.get("last_reply_ts")
    value = str(raw or "")
    return value if value else parent_ts
