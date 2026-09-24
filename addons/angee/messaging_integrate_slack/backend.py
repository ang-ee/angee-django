"""Slack event-feed partitions: serial, bounded polling over ``slack_sdk``.

Each conversation's ``SyncStream.cursor`` retains a history watermark/page
cursor and a bounded per-thread reply watermark::

    {
        "conversation": {
            "last_ts": "1784700000.000100",
            "history": {
                "cursor": "next-page",
                "oldest": "1784700000.000100",
                "last_ts": "1784700010.000100",
            },
        },
        "threads": {"1784700000.000100": "1784700005.000100"},
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
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from typing import Any, ClassVar, TypeVar

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from angee.integrate.streams import CursorInvalid, StreamDefinition, StreamPage
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
    cursor: str = ""
    after_ts: str = ""


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
    sync_parallelism = 1

    client_class: ClassVar[type[WebClient]] = WebClient
    """Official protocol client factory; tests substitute an in-memory client."""

    def __init__(self, integration: object) -> None:
        """Bind the channel and initialize this run's paging/user caches."""

        super().__init__(integration)
        self._client: WebClient | None = None
        self._work: deque[_ConversationWork] | None = None
        self._users: dict[str, dict[str, Any]] | None = None
        self._conversations: dict[str, dict[str, Any]] = {}
        self._stream_identity: tuple[str, int] | None = None
        self._cursor: dict[str, Any] = {}
        self._page_bound = _PAGE_LIMIT
        self._credential: Any = None

    def streams(self, *, deadline: float | None = None) -> tuple[StreamDefinition, ...]:
        """Discover conversations once for this serial backend."""

        self._conversations = self._discover_conversations(deadline=deadline)
        return tuple(StreamDefinition(key="messages", partition=key) for key in sorted(self._conversations))

    def seed_cursor(self, stream: Any, legacy_cursor: dict[str, Any]) -> dict[str, Any] | None:
        """Translate one retained conversation and its thread watermarks once."""

        conversation = (legacy_cursor.get("conversations") or {}).get(stream.partition)
        threads = (legacy_cursor.get("threads") or {}).get(stream.partition)
        if not conversation and not threads:
            return None
        return {"conversation": deepcopy(conversation or {}), "threads": deepcopy(threads or {})}

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Read one conversation page; its cursor commits with the ingested messages."""

        self._load_credentials()
        identity = (stream.partition, stream.generation)
        if self._stream_identity != identity:
            if stream.partition not in self._conversations:
                self._conversations = self._discover_conversations(deadline=deadline)
            self._stream_identity = identity
            self._cursor = deepcopy(stream.cursor)
            self._work = deque([self._conversation_work(self._conversations[stream.partition])])
        self._page_bound = max(1, page_bound)
        while self._work:
            work = self._work[0]
            if not work.history_done:
                batch = self._history_batch(work, deadline=deadline)
                if batch:
                    return StreamPage(records=batch, cursor=deepcopy(self._cursor), exhausted=False)
                if not work.history_done:
                    continue
            batch = self._reply_batch(work, deadline=deadline)
            if batch:
                return StreamPage(records=batch, cursor=deepcopy(self._cursor), exhausted=False)
            if work.replies:
                continue
            self._work.popleft()
        return StreamPage(records=(), cursor=deepcopy(self._cursor), exhausted=True)

    def _conversation_work(self, conversation: dict[str, Any]) -> _ConversationWork:
        """Build one conversation plan, including its bounded active-thread set."""

        work = _ConversationWork(conversation=conversation)
        threads = self._active_threads()
        for parent_ts in sorted(threads, key=_timestamp_text_key):
            self._queue_reply(work, parent_ts, threads[parent_ts])
        return work

    def _discover_conversations(self, *, deadline: float | None = None) -> dict[str, dict[str, Any]]:
        """List all visible conversations once for this serial backend instance."""

        self._load_credentials()
        conversations: dict[str, dict[str, Any]] = {}
        cursor = ""
        while True:
            response = self._api_call(
                self._client_or_create().users_conversations,
                deadline=deadline,
                user=self._own_id(),
                types=_CONVERSATION_TYPES,
                exclude_archived=True,
                limit=_PAGE_LIMIT,
                cursor=cursor or None,
            )
            data = response_data(response)
            for raw in data.get("channels") or ():
                if isinstance(raw, Mapping) and raw.get("id"):
                    conversations[str(raw["id"])] = dict(raw)
            cursor = _next_cursor(data)
            if not cursor:
                break
        return conversations

    def _history_batch(self, work: _ConversationWork, *, deadline: float | None) -> list[ParsedMessage]:
        """Consume at most one configured slice from the current history page."""

        if work.history_page is None:
            work.history_page = self._history_page(work, deadline=deadline)
            if not work.history_page.messages:
                self._finish_history_page(work)
                return []
        page = work.history_page
        batch: list[ParsedMessage] = []
        used_bytes = 0
        while page.messages and len(batch) < self._batch_size():
            raw = page.messages[0]
            message = self._parse_without_media(raw, work.conversation, deadline=deadline)
            if (
                message is not None
                and batch
                and self._estimated_media_bytes(raw) > self._max_batch_bytes() - used_bytes
            ):
                break
            page.messages.popleft()
            page.after_ts = _timestamp(raw)
            self._remember_thread(work, raw)
            if message is None:
                continue
            media, downloaded = self._media(raw, byte_budget=max(0, self._max_batch_bytes() - used_bytes))
            batch.append(message.with_media(media))
            used_bytes += downloaded
        if not page.messages:
            self._finish_history_page(work)
        else:
            self._conversation_cursor()["history"] = {
                "cursor": page.cursor,
                "after_ts": page.after_ts,
                "oldest": page.oldest,
                "last_ts": page.last_ts,
            }
        return batch

    def _history_page(self, work: _ConversationWork, *, deadline: float | None) -> _HistoryPage:
        """Fetch one newest-first history page from its durable resume point."""

        channel_id = str(work.conversation["id"])
        entry = self._conversation_cursor()
        scan = entry.get("history")
        scan_values: Mapping[str, Any] = scan if isinstance(scan, Mapping) else {}
        cursor = str(scan_values.get("cursor") or "")
        last_ts = str(scan_values.get("last_ts") or "")
        oldest = str(scan_values.get("oldest") or self._oldest())
        after_ts = str(scan_values.get("after_ts") or "")
        try:
            data = self._history_response(
                channel_id, oldest=oldest, cursor=cursor, latest=last_ts if after_ts else "", deadline=deadline
            )
        except SlackApiError as error:
            if not cursor or str(response_data(error.response).get("error") or "") != "invalid_cursor":
                raise
            # Only the history pagination state expired; conversation and
            # thread watermarks remain valid in the successor generation.
            retained = deepcopy(self._cursor)
            retained["conversation"].pop("history", None)
            raise CursorInvalid(cursor=retained) from error
        messages = [dict(raw) for raw in data.get("messages") or () if isinstance(raw, Mapping)]
        target = _latest_timestamp(messages, floor=last_ts)
        return _HistoryPage(
            messages=deque(
                raw
                for raw in sorted(messages, key=_timestamp_key)
                if not after_ts or _timestamp_key(raw) > _timestamp_text_key(after_ts)
            ),
            next_cursor=_next_cursor(data),
            last_ts=target,
            oldest=oldest,
            cursor=cursor,
            after_ts=after_ts,
        )

    def _history_response(
        self, channel_id: str, *, oldest: str, cursor: str, latest: str = "", deadline: float | None
    ) -> Mapping[str, Any]:
        """Call one bounded Slack history page."""

        response = self._api_call(
            self._client_or_create().conversations_history,
            deadline=deadline,
            channel=channel_id,
            oldest=oldest,
            inclusive=bool(latest),
            latest=latest or None,
            limit=min(_PAGE_LIMIT, self._batch_size()),
            cursor=cursor or None,
        )
        return response_data(response)

    def _finish_history_page(self, work: _ConversationWork) -> None:
        """Persist the next page cursor, or commit the completed snapshot watermark."""

        page = work.history_page
        if page is None:
            return
        entry = self._conversation_cursor()
        if page.next_cursor:
            entry["history"] = {"cursor": page.next_cursor, "oldest": page.oldest, "last_ts": page.last_ts}
        else:
            if page.last_ts:
                entry["last_ts"] = page.last_ts
            entry.pop("history", None)
            work.history_done = True
        work.history_page = None

    def _reply_batch(self, conversation: _ConversationWork, *, deadline: float | None) -> list[ParsedMessage]:
        """Return one earliest-first slice from an active thread reply rescan."""

        channel_id = str(conversation.conversation["id"])
        while conversation.replies:
            work = conversation.replies[0]
            if work.page is None:
                response = self._api_call(
                    self._client_or_create().conversations_replies,
                    deadline=deadline,
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
                message = self._parse_without_media(raw, conversation.conversation, deadline=deadline)
                if (
                    message is not None
                    and batch
                    and self._estimated_media_bytes(raw) > self._max_batch_bytes() - used_bytes
                ):
                    break
                page.messages.popleft()
                self._advance_thread_cursor(work.parent_ts, timestamp)
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
        threads = self._cursor_threads()
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

    def _active_threads(self) -> dict[str, str]:
        """Prune and return thread parents active inside the configured backfill window."""

        threads = self._cursor.get("threads")
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

    def _advance_thread_cursor(self, parent_ts: str, timestamp: str) -> None:
        """Advance one earliest-first thread reply watermark."""

        threads = self._cursor_threads()
        current = _thread_watermark(threads.get(parent_ts), parent_ts)
        if _timestamp_text_key(timestamp) > _timestamp_text_key(current):
            threads[parent_ts] = timestamp

    def _parse_without_media(
        self,
        raw: Mapping[str, Any],
        conversation: Mapping[str, Any],
        *,
        deadline: float | None,
    ) -> ParsedMessage | None:
        """Reject transport noise before resolving any attachment bytes."""

        return parsed_message(
            raw,
            conversation=conversation,
            team_id=self._team_id(),
            own_id=self._own_id(),
            users=self._user_cache(deadline=deadline),
        )

    def _user_cache(self, *, deadline: float | None) -> dict[str, dict[str, Any]]:
        """Return this serial backend instance's paginated ``users.list`` cache."""

        if self._users is not None:
            return self._users
        users: dict[str, dict[str, Any]] = {}
        cursor = ""
        while True:
            response = self._api_call(
                self._client_or_create().users_list,
                deadline=deadline,
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

    def _oldest(self) -> str:
        """Return the saved watermark, or the configured bounded-backfill floor."""

        entry = self._cursor.get("conversation")
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

        return max(1, min(self._page_bound, _PAGE_LIMIT, int(self._config().get("batch_size") or _DEFAULT_BATCH_SIZE)))

    def _max_media_bytes(self) -> int:
        """Return the per-file download cap."""

        return max(1, int(self._config().get("max_media_bytes") or _DEFAULT_MAX_MEDIA_BYTES))

    def _max_batch_bytes(self) -> int:
        """Return the aggregate downloaded-media cap for one returned slice."""

        return max(1, int(self._config().get("max_batch_bytes") or _DEFAULT_MAX_BATCH_BYTES))

    def _conversation_cursor(self) -> dict[str, Any]:
        """Return one mutable conversation cursor entry."""

        entry = self._cursor.get("conversation")
        if not isinstance(entry, dict):
            entry = {}
            self._cursor["conversation"] = entry
        return entry

    def _cursor_threads(self) -> dict[str, Any]:
        """Return one conversation's mutable thread-reply watermark map."""

        threads = self._cursor.get("threads")
        if not isinstance(threads, dict):
            threads = {}
            self._cursor["threads"] = threads
        return threads

    def _load_credentials(self) -> None:
        """Reload authentication and update the reused client before each page."""

        self._credential = self.bridge.fresh_credential()
        if self._client is not None:
            self._client.token = self._token()

    def _client_or_create(self) -> WebClient:
        """Return the token-authenticated official Slack client."""

        if self._client is None:
            self._client = self.client_class(token=self._token())
        return self._client

    def _token(self) -> str:
        """Return the channel's static user OAuth token."""

        credential = self._credential
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

    def _api_call(self, operation: Callable[..., _T], *, deadline: float | None = None, **kwargs: Any) -> _T:
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
                if deadline is not None and delay >= deadline - monotonic():
                    raise SlackRateLimitError(
                        "Slack sync time budget exhausted while rate limited; resume next poll."
                    ) from error
                sleep(delay)
                if deadline is not None and monotonic() >= deadline:
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
