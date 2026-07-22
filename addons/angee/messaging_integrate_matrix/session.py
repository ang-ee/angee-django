"""Live Matrix user session — mautrix protocol and crypto, worker-only."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mautrix.client import Client
from mautrix.client.state_store import FileStateStore
from mautrix.crypto import OlmMachine
from mautrix.crypto.attachments import decrypt_attachment
from mautrix.crypto.store.asyncpg import PgCryptoStore
from mautrix.types import (
    EventType,
    Filter,
    LoginType,
    PaginationDirection,
    PresenceState,
    RoomEventFilter,
    RoomFilter,
    StateFilter,
)
from mautrix.util.async_db import Database

from angee.integrate.live import STOP_JOIN_SECONDS
from angee.integrate.session import PASSWORD_SKIPPED
from angee.messaging.session import INGEST_CHUNK, LiveChannelSession
from angee.messaging_integrate_matrix.connect import matrix_login
from angee.messaging_integrate_matrix.identity import MatrixMediaFact, parsed_message

logger = logging.getLogger(__name__)

API_TIMEOUT_SECONDS = 30.0
DOWNLOAD_TIMEOUT_SECONDS = 60.0
INITIAL_HISTORY_TIMEOUT_SECONDS = 60.0
INITIAL_HISTORY_LIMIT = 100
INITIAL_ROOM_LIMIT = 20
SYNC_TIMEOUT_MILLISECONDS = 30_000

_SESSION_FILE = "session.json"
_STATE_STORE_FILE = "state.pickle"
_CRYPTO_STORE_FILE = "crypto.db"
_RECOVERY_PROMPT = (
    "Enter the Matrix recovery key to establish cross-signing and trust this device, "
    "or skip to continue with forward decryption. Earlier history is best-effort through "
    "to-device key sharing after verification."
)


class MatrixSession(LiveChannelSession):
    """One Matrix account whose asyncio loop belongs to its connection thread."""

    session_file_name = _SESSION_FILE
    client_class: type[Any] = Client
    state_store_class: type[Any] = FileStateStore
    database_class: type[Any] = Database
    crypto_store_class: type[Any] = PgCryptoStore
    olm_machine_class: type[Any] = OlmMachine
    decrypt_attachment: Any = staticmethod(decrypt_attachment)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._main_task: asyncio.Task[Any] | None = None
        self._session_path: Path | None = None
        self._session_facts: dict[str, Any] = {}
        self._login_material: tuple[str, str] | None = None
        self._crypto_db: Any = None
        self._crypto_store: Any = None
        self._room_names: dict[str, str] = {}
        self._member_names: dict[str, dict[str, str]] = {}
        self._joined_room_ids: set[str] = set()
        self._undecryptable_events = 0

    def _build_client(self, session_path: Path) -> Any:
        """Build mautrix against retained login, room-state, and crypto stores."""

        self._session_path = session_path
        self._session_facts = _read_session_facts(session_path)
        if not self._session_facts.get("access_token"):
            credential = self._fresh_credential()
            if credential is None:
                raise ValueError("This Matrix channel has no basic-auth credential.")
            self._login_material = matrix_login(credential)
        homeserver = str(self.bridge.subscription_state.get("homeserver") or "").strip()
        if not homeserver:
            raise ValueError("This Matrix channel has no homeserver URL.")
        state_store = self.state_store_class(session_path.with_name(_STATE_STORE_FILE))
        return self.client_class(
            mxid=str(self._session_facts.get("user_id") or ""),
            device_id=str(self._session_facts.get("device_id") or ""),
            base_url=homeserver,
            token=str(self._session_facts.get("access_token") or "") or None,
            state_store=state_store,
        )

    def _connect(self) -> None:
        """Own the mautrix asyncio loop and all SDK calls on this vendor thread."""

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._main_task = loop.create_task(self._run_client())
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            if _matrix_errcode(error) in {"M_MISSING_TOKEN", "M_UNKNOWN_TOKEN"}:
                self.events.put(("logged_out", None))
            else:
                self.outcome_error = error
                logger.exception("Matrix connection for channel %s crashed.", self.bridge.sqid)
        finally:
            try:
                loop.run_until_complete(self._close_client())
            except Exception:
                logger.info("Closing Matrix channel %s did not finish cleanly.", self.bridge.sqid)
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            asyncio.set_event_loop(None)
            loop.close()
            self.events.put(("disconnected", None))

    async def _run_client(self) -> None:
        """Authenticate, initialize E2EE, recover keys, then sync.

        The first sync pass performs the bounded initial backfill (``_sync_loop``
        → ``_initial_history``); backfill is not a separate step before sync.
        """

        await self.client.state_store.open()
        try:
            own_id = await self._authenticate()
        except Exception as error:
            if _matrix_errcode(error) == "M_FORBIDDEN":
                self.events.put(("logged_out", None))
                return
            raise
        if self._vendor_stopping():
            return
        crypto = await self._open_crypto(own_id)
        self.client.add_event_handler(EventType.ROOM_MESSAGE, self._on_room_message, wait_sync=True)
        if not await self._recover_keys(crypto):
            return
        self.events.put(("paired", own_id))
        await self._sync_loop()

    async def _authenticate(self) -> str:
        """Resume a native access token or perform one m.login.password login."""

        token = str(self._session_facts.get("access_token") or "")
        if token:
            whoami = await self._bounded(self.client.whoami())
            own_id = str(whoami.user_id)
            device_id = str(whoami.device_id or self._session_facts.get("device_id") or "")
        else:
            if self._login_material is None:
                raise RuntimeError("The Matrix login material was not prepared.")
            username, password = self._login_material
            login = await self._bounded(
                self.client.login(
                    identifier=username,
                    login_type=LoginType.PASSWORD,
                    password=password,
                    device_name="Angee",
                    store_access_token=True,
                    update_hs_url=False,
                )
            )
            own_id = str(login.user_id)
            device_id = str(login.device_id)
            token = str(login.access_token)
        if not own_id or not device_id or not token:
            raise RuntimeError("Matrix login returned incomplete session facts.")
        self.client.mxid = own_id
        self.client.device_id = device_id
        self.client.api.token = token
        self._session_facts.update(
            user_id=own_id,
            device_id=device_id,
            access_token=token,
            pickle_key=str(self._session_facts.get("pickle_key") or secrets.token_urlsafe(32)),
        )
        self._persist_session_facts()
        return own_id

    async def _open_crypto(self, own_id: str) -> Any:
        """Open mautrix's SQLite crypto store and attach its Olm machine."""

        if self._session_path is None:
            raise RuntimeError("The Matrix session path is unavailable.")
        database = self.database_class.create(
            f"sqlite:///{self._session_path.with_name(_CRYPTO_STORE_FILE)}",
            upgrade_table=self.crypto_store_class.upgrade_table,
        )
        await database.start()
        self._crypto_db = database
        store = self.crypto_store_class(
            account_id=own_id,
            pickle_key=str(self._session_facts["pickle_key"]),
            db=database,
        )
        await store.open()
        self._crypto_store = store
        crypto = self.olm_machine_class(
            self.client,
            store,
            _MatrixCryptoStateStore(self.client.state_store, self._joined_room_ids),
        )
        await crypto.load()
        stored_device_id = await store.get_device_id()
        if stored_device_id and str(stored_device_id) != str(self.client.device_id):
            raise RuntimeError("The Matrix crypto store belongs to a different device.")
        await store.put_device_id(self.client.device_id)
        if not crypto.account.shared:
            await crypto.share_keys()
        self.client.sync_store = store
        self.client.crypto = crypto
        return crypto

    async def _recover_keys(self, crypto: Any) -> bool:
        """Run the optional recovery-key round once for this retained store."""

        if self._session_facts.get("recovery") in {"verified", "skipped"}:
            return True
        recovery_key = await asyncio.to_thread(
            self.request_password,
            _RECOVERY_PROMPT,
            material_key="recovery_key",
            optional=True,
        )
        if recovery_key is None:
            return False
        if recovery_key is PASSWORD_SKIPPED:
            self._session_facts["recovery"] = "skipped"
        else:
            # mautrix owns SSSS key import, cross-signing self-verification, and
            # the signature upload through this single recovery-key operation.
            await self._bounded(crypto.verify_with_recovery_key(recovery_key))
            self._session_facts["recovery"] = "verified"
        self._persist_session_facts()
        return True

    async def _sync_loop(self) -> None:
        """Process sync responses with a persisted token and lazy member filter."""

        filter_id = await self._bounded(
            self.client.create_filter(
                Filter(
                    room=RoomFilter(
                        state=StateFilter(lazy_load_members=True),
                        timeline=RoomEventFilter(lazy_load_members=True),
                    )
                )
            )
        )
        since = await self._get_next_batch()
        first = True
        while not self._vendor_stopping():
            raw = await self.client.sync(
                since=since,
                timeout=0 if first else SYNC_TIMEOUT_MILLISECONDS,
                filter_id=filter_id,
                set_presence=PresenceState.OFFLINE,
            )
            if not isinstance(raw, Mapping):
                raise RuntimeError("Matrix sync returned a non-object response.")
            self._remember_room_facts(raw)
            tasks = self.client.handle_sync(raw)
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                failures = [result for result in results if isinstance(result, Exception)]
                if failures:
                    logger.warning(
                        "Matrix sync for channel %s skipped %s failed event handler(s).",
                        self.bridge.sqid,
                        len(failures),
                    )
                for result in results:
                    if isinstance(result, BaseException) and not isinstance(result, Exception):
                        raise result
            if first and not self.bridge.subscription_state.get("history_seeded"):
                completed = await self._initial_history(raw)
                if completed:
                    self.events.put(("history_seeded", None))
            next_batch = str(raw.get("next_batch") or "")
            if next_batch:
                if not await self._checkpoint_sync_batch(next_batch):
                    return
                since = next_batch
            first = False
            if self._vendor_stopping():
                return
            await asyncio.sleep(0)

    async def _initial_history(self, sync: Mapping[str, Any]) -> bool:
        """Queue at most 100 older events, capped at 20 per joined room."""

        rooms = _joined_rooms(sync)
        remaining = INITIAL_HISTORY_LIMIT
        batch: list[tuple[Any, Any]] = []
        completed = True
        try:
            async with asyncio.timeout(INITIAL_HISTORY_TIMEOUT_SECONDS):
                for room_id, room in rooms:
                    if remaining <= 0 or self._vendor_stopping():
                        break
                    timeline = _mapping(room.get("timeline")) or {}
                    from_token = str(timeline.get("prev_batch") or "") or None
                    limit = min(INITIAL_ROOM_LIMIT, remaining)
                    page = await self._bounded(
                        self.client.get_messages(
                            room_id,
                            direction=PaginationDirection.BACKWARD,
                            from_token=from_token,
                            limit=limit,
                            filter_json={"lazy_load_members": True},
                        )
                    )
                    events = list(page.events)
                    for event in reversed(events):
                        queued = await self._queued_event(event, room_id=room_id)
                        if queued is None:
                            continue
                        batch.append(queued)
                        remaining -= 1
                        if len(batch) == INGEST_CHUNK:
                            self.events.put(("messages", batch))
                            batch = []
                        if remaining <= 0:
                            break
        except TimeoutError:
            completed = False
            logger.info("Matrix initial history for channel %s reached its bound.", self.bridge.sqid)
        if batch:
            self.events.put(("messages", batch))
        return completed and not self._vendor_stopping()

    async def _on_room_message(self, event: Any) -> None:
        """Translate one dispatched (and, when needed, decrypted) room event."""

        queued = await self._queued_event(event)
        if queued is not None:
            self.events.put(("messages", [queued]))

    async def _queued_event(self, event: Any, *, room_id: str = "") -> tuple[Any, Any] | None:
        """Return one neutral message plus the raw event used for media download."""

        wire = _event_mapping(event, room_id=room_id)
        if wire.get("type") == "m.room.encrypted":
            try:
                decrypted = await self.client.crypto.decrypt_megolm_event(event)
            except Exception as error:
                self._undecryptable_events += 1
                logger.info(
                    "Skipping undecryptable Matrix event for channel %s (total %s): %s",
                    self.bridge.sqid,
                    self._undecryptable_events,
                    error,
                )
                return None
            wire = _event_mapping(decrypted, room_id=room_id)
        resolved_room = str(room_id or wire.get("room_id") or "")
        sender = str(wire.get("sender") or "")
        display_name = self._member_names.get(resolved_room, {}).get(sender, "")
        if display_name:
            wire["sender_display_name"] = display_name
        parsed = parsed_message(
            wire,
            room_id=resolved_room,
            room_name=self._room_names.get(resolved_room, ""),
            own_user_id=self.client.mxid,
        )
        return (parsed, wire) if parsed is not None else None

    def _remember_room_facts(self, sync: Mapping[str, Any]) -> None:
        """Cache mutable room/member labels from the current raw sync.

        Read from the raw sync rather than ``client.state_store`` on purpose:
        the sync filters use ``lazy_load_members=True``, so the state store is
        not guaranteed to carry every sender's profile at first sight, while the
        raw timeline events do.
        """

        for room_id, room in _joined_rooms(sync):
            self._joined_room_ids.add(room_id)
            state = _mapping(room.get("state")) or {}
            timeline = _mapping(room.get("timeline")) or {}
            events = (*_sequence(state.get("events")), *_sequence(timeline.get("events")))
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                event_type = str(event.get("type") or "")
                content = _mapping(event.get("content")) or {}
                if event_type == "m.room.name" and (name := str(content.get("name") or "").strip()):
                    self._room_names[room_id] = name
                elif event_type == "m.room.canonical_alias" and room_id not in self._room_names:
                    alias = str(content.get("alias") or "").strip()
                    if alias:
                        self._room_names[room_id] = alias
                elif event_type == "m.room.member":
                    user_id = str(event.get("state_key") or "").strip()
                    display_name = str(content.get("displayname") or "").strip()
                    if user_id and display_name:
                        self._member_names.setdefault(room_id, {})[user_id] = display_name

    def _handle(self, kind: str, payload: Any) -> bool:
        """Persist the one initial-history gate; delegate message ingest."""

        if kind == "history_seeded":
            self.bridge.merge_subscription_state(history_seeded=True)
            return self._still_wanted()
        if kind == "sync_checkpoint":
            next_batch, persisted, done = payload
            future: Any = None
            try:
                if self._loop is None or self._loop.is_closed() or not self._loop.is_running():
                    raise RuntimeError("The Matrix event loop is unavailable for a sync checkpoint.")
                future = asyncio.run_coroutine_threadsafe(self._put_next_batch(next_batch), self._loop)
                future.result(timeout=API_TIMEOUT_SECONDS)
                persisted.set()
            except Exception as error:
                if future is not None:
                    future.cancel()
                self.outcome_error = error
                logger.exception("Matrix sync checkpoint failed for channel %s.", self.bridge.sqid)
                return False
            finally:
                done.set()
            return self._still_wanted()
        return super()._handle(kind, payload)

    def _download(self, _payload: Any, fact: MatrixMediaFact) -> bytes | None:
        """Download authenticated Matrix media and decrypt encrypted attachments."""

        loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            return None
        future = asyncio.run_coroutine_threadsafe(self._download_media(fact.url), loop)
        try:
            content = future.result(timeout=DOWNLOAD_TIMEOUT_SECONDS)
            raw = bytes(content)
            if fact.encrypted:
                raw = self.decrypt_attachment(raw, fact.key, fact.hash, fact.iv)
            return raw
        except Exception:
            future.cancel()
            logger.info("Matrix media download failed for channel %s.", self.bridge.sqid)
            return None

    async def _download_media(self, mxc_url: str) -> bytes:
        """Let mautrix negotiate authenticated or legacy Matrix media download."""

        return bytes(await self.client.download_media(mxc_url))

    async def _bounded(self, awaitable: Any) -> Any:
        """Await one Matrix API operation with a finite bound."""

        return await asyncio.wait_for(awaitable, timeout=API_TIMEOUT_SECONDS)

    async def _get_next_batch(self) -> str | None:
        """Read the native crypto-store sync token."""

        return await self._crypto_store.get_next_batch()

    async def _put_next_batch(self, next_batch: str) -> None:
        """Persist one task-thread-acknowledged sync token in the crypto store."""

        await self._crypto_store.put_next_batch(next_batch)

    async def _checkpoint_sync_batch(self, next_batch: str) -> bool:
        """Wait until queued messages land before advancing the sync watermark."""

        persisted = threading.Event()
        done = threading.Event()
        self.events.put(("sync_checkpoint", (next_batch, persisted, done)))
        while not done.is_set():
            if self._vendor_stopping():
                return False
            await asyncio.sleep(0.01)
        return persisted.is_set()

    async def _close_client(self) -> None:
        """Flush native stores and close network/database resources on their loop."""

        if self.client is not None:
            await self.client.state_store.flush()
            await self.client.state_store.close()
            await self.client.api.session.close()
        if self._crypto_store is not None:
            await self._crypto_store.close()
            self._crypto_store = None
        if self._crypto_db is not None:
            await self._crypto_db.stop()
            self._crypto_db = None

    def _shutdown(self, connection: threading.Thread) -> bool:
        """Cancel the Matrix loop and join its thread within one shared deadline."""

        deadline = time.monotonic() + STOP_JOIN_SECONDS
        loop = self._loop
        task = self._main_task
        if loop is not None and not loop.is_closed() and loop.is_running() and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        connection.join(timeout=max(0.0, deadline - time.monotonic()))
        return not connection.is_alive()

    def _persist_session_facts(self) -> None:
        """Atomically persist mautrix login and sync facts beside its stores."""

        if self._session_path is None:
            raise RuntimeError("The Matrix session path is unavailable.")
        _write_session_facts(self._session_path, self._session_facts)


def _joined_rooms(sync: Mapping[str, Any]) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Return joined room mappings in deterministic room-id order."""

    rooms = _mapping(sync.get("rooms")) or {}
    joined = _mapping(rooms.get("join")) or {}
    return tuple(
        (str(room_id), room)
        for room_id, room in sorted(joined.items(), key=lambda item: str(item[0]))
        if isinstance(room, Mapping)
    )


class _MatrixCryptoStateStore:
    """Adapt mautrix's client state store to the smaller crypto-state contract."""

    def __init__(self, state_store: Any, joined_rooms: set[str]) -> None:
        self.state_store = state_store
        self.joined_rooms = joined_rooms

    async def is_encrypted(self, room_id: str) -> bool:
        """Delegate the room-encryption fact to mautrix's state owner."""

        return await self.state_store.is_encrypted(room_id)

    async def get_encryption_info(self, room_id: str) -> Any:
        """Delegate the room encryption settings to mautrix's state owner."""

        return await self.state_store.get_encryption_info(room_id)

    async def find_shared_rooms(self, _user_id: str) -> list[str]:
        """Return joined rooms currently known by this client's sync stream."""

        return sorted(self.joined_rooms)


def _event_mapping(event: Any, *, room_id: str = "") -> dict[str, Any]:
    """Serialize a mautrix event into the console-safe identity boundary."""

    if isinstance(event, Mapping):
        wire = dict(event)
    else:
        wire = dict(event.serialize())
    resolved_room = room_id or str(wire.get("room_id") or getattr(event, "room_id", "") or "")
    if resolved_room:
        wire["room_id"] = resolved_room
    return wire


def _read_session_facts(path: Path) -> dict[str, Any]:
    """Read the retained login/sync envelope, treating a missing file as new."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise ValueError(f"The Matrix session file {path} is invalid.") from error
    if not isinstance(value, dict):
        raise ValueError(f"The Matrix session file {path} must contain an object.")
    return value


def _write_session_facts(path: Path, facts: Mapping[str, Any]) -> None:
    """Write one private, replace-safe login/sync envelope."""

    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(dict(facts), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def _matrix_errcode(error: Exception) -> str:
    """Return mautrix's Matrix error code in its stable wire spelling."""

    value = getattr(error, "errcode", "")
    return str(getattr(value, "value", value) or "").upper()


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: object) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray) else ()
