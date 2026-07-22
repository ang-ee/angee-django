"""Tests for the Matrix bridge with fake worker-only mautrix modules."""

from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
from angee.integrate.live import PairingState, session_store_path
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.sync import BridgeProgressReporter
from angee.messaging.connect import skip_channel_password, submit_channel_password
from angee.messaging_integrate_matrix.backend import MatrixChannelBackend
from angee.messaging_integrate_matrix.constants import SESSION_QUEUE
from angee.messaging_integrate_matrix.identity import MatrixMediaFact, parsed_message
from tests.conftest import (
    SchemaAddon,
    Vendor,
    _clear_model_tables,
    _create_missing_tables,
    execute_schema,
    result_data,
)
from tests.test_messaging import MESSAGING_TEST_MODELS, Message
from tests.test_messaging_graphql import (
    Channel,
    _platform_admin,
    _request,
    iam_schema,
    integrate_schema,
    messaging_schema,
    parties_schema,
)

Credential = apps.get_model("integrate", "Credential")
MATRIX_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel)


def _event(
    event_id: str = "$event",
    *,
    room_id: str = "!room:example.com",
    sender: str = "@grace:example.com",
    msgtype: str = "m.text",
    body: str = "Hello from Matrix",
    relation: dict[str, Any] | None = None,
    **content: Any,
) -> dict[str, Any]:
    """Return one ordinary Matrix event-dict fixture."""

    return {
        "type": "m.room.message",
        "room_id": room_id,
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": 1_768_800_000_000,
        "content": {
            "msgtype": msgtype,
            "body": body,
            **({"m.relates_to": relation} if relation is not None else {}),
            **content,
        },
    }


def test_matrix_identity_scopes_events_and_replies_to_the_room() -> None:
    """Room, sender, message, and reply identities use stable Matrix ids."""

    wire = _event(
        "$reply",
        relation={"m.in_reply_to": {"event_id": "$parent"}},
    )
    parsed = parsed_message(wire, room_name="Angee", own_user_id="@ada:example.com")

    assert parsed is not None
    assert parsed.external_id == "!room:example.com/$reply"
    assert parsed.platform == "matrix"
    assert parsed.direction == "inbound"
    assert parsed.in_reply_to == "!room:example.com/$parent"
    assert parsed.thread is not None
    assert parsed.thread.external_id == "!room:example.com"
    assert parsed.thread.title == "Angee"
    assert parsed.sender is not None
    assert parsed.sender.platform == "matrix"
    assert parsed.sender.external_id == "@grace:example.com"
    assert parsed.body is not None and parsed.body.text == "Hello from Matrix"


@pytest.mark.parametrize("msgtype", ["m.text", "m.notice", "m.emote"])
def test_matrix_text_message_types_map_to_text(msgtype: str) -> None:
    """The three v1 text-like msgtypes share the neutral text body."""

    parsed = parsed_message(_event(msgtype=msgtype))
    assert parsed is not None
    assert parsed.body is not None and parsed.body.text == "Hello from Matrix"


@pytest.mark.parametrize("msgtype", ["m.image", "m.file", "m.audio", "m.video"])
def test_matrix_media_types_expose_download_and_encryption_facts(msgtype: str) -> None:
    """Plain and encrypted media stay as facts until the vendor loop downloads them."""

    parsed = parsed_message(
        _event(
            msgtype=msgtype,
            body="asset.bin",
            file={
                "url": "mxc://example.com/encrypted",
                "key": {"k": "key"},
                "hashes": {"sha256": "hash"},
                "iv": "iv",
            },
            info={"mimetype": "application/octet-stream"},
        )
    )
    assert parsed is not None
    assert parsed.metadata["_media_facts"] == (
        MatrixMediaFact(
            url="mxc://example.com/encrypted",
            mime="application/octet-stream",
            name="asset.bin",
            key="key",
            hash="hash",
            iv="iv",
        ),
    )


@pytest.mark.parametrize(
    "wire",
    [
        {**_event(), "state_key": ""},
        {**_event(), "type": "m.reaction"},
        {**_event(), "type": "m.room.redaction"},
        _event(msgtype="m.location"),
        _event(relation={"rel_type": "m.replace", "event_id": "$old"}),
    ],
)
def test_matrix_v1_skip_list_ignores_non_messages_and_edits(wire: dict[str, Any]) -> None:
    """Unsupported Matrix event shapes never reach the neutral ingest seam."""

    assert parsed_message(wire) is None


def test_matrix_backend_declares_worker_and_transient_material_contracts() -> None:
    """Console imports see only the dotted worker boundary and recovery-key reset policy."""

    assert SESSION_QUEUE == "matrix"
    assert MatrixChannelBackend.key == "matrix"
    assert MatrixChannelBackend.session_class == ("angee.messaging_integrate_matrix.session.MatrixSession")
    assert MatrixChannelBackend.transient_material_keys == ("recovery_key",)


def test_matrix_real_crypto_store_imports_and_round_trips_sqlite_device_id(tmp_path: Path) -> None:
    """The installed E2EE boundary includes both DB drivers and a usable SQLite store."""

    import aiosqlite  # noqa: F401 — importing both drivers is part of the assertion.
    import asyncpg  # noqa: F401
    import olm  # noqa: F401
    from mautrix.crypto import OlmAccount
    from mautrix.crypto.store.asyncpg import PgCryptoStore
    from mautrix.types import DeviceID
    from mautrix.util.async_db import Database

    async def round_trip() -> None:
        database = Database.create(
            f"sqlite:///{tmp_path / 'crypto.db'}",
            upgrade_table=PgCryptoStore.upgrade_table,
        )
        await database.start()
        try:
            store = PgCryptoStore("@ada:example.com", "pickle-key", database)
            await store.open()
            await store.put_device_id(DeviceID("ANGEEDEVICE"))
            await store.put_account(OlmAccount())
            await store.close()

            reopened = PgCryptoStore("@ada:example.com", "pickle-key", database)
            await reopened.open()
            assert str(await reopened.get_device_id()) == "ANGEEDEVICE"
            await reopened.close()
        finally:
            await database.stop()

    asyncio.run(round_trip())


class _FakeMatrixError(Exception):
    def __init__(self, errcode: str) -> None:
        self.errcode = errcode
        super().__init__(errcode)


class _FakeStateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.opened = False
        self.closed = False

    async def open(self) -> None:
        self.opened = True

    async def flush(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    async def is_encrypted(self, _room_id: str) -> bool:
        return False

    async def get_encryption_info(self, _room_id: str) -> Any:
        return None


class _FakeDatabase:
    instances: list[_FakeDatabase] = []

    def __init__(self, url: str, upgrade_table: Any) -> None:
        self.url = url
        self.upgrade_table = upgrade_table
        self.started = False
        self.stopped = False
        type(self).instances.append(self)

    @classmethod
    def create(cls, url: str, *, upgrade_table: Any) -> _FakeDatabase:
        return cls(url, upgrade_table)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FakeCryptoStore:
    upgrade_table = object()
    instances: list[_FakeCryptoStore] = []

    def __init__(self, *, account_id: str, pickle_key: str, db: Any) -> None:
        self.account_id = account_id
        self.pickle_key = pickle_key
        self.db = db
        self.device_id = ""
        self.next_batch: str | None = None
        type(self).instances.append(self)

    async def open(self) -> None:
        return None

    async def put_device_id(self, device_id: str) -> None:
        self.device_id = device_id

    async def get_device_id(self) -> str:
        return self.device_id

    async def put_next_batch(self, next_batch: str) -> None:
        self.next_batch = next_batch

    async def get_next_batch(self) -> str | None:
        return self.next_batch

    async def close(self) -> None:
        return None


class _FakeOlmMachine:
    instances: list[_FakeOlmMachine] = []
    decrypt_error: Exception | None = None

    def __init__(self, client: Any, store: Any, state_store: Any) -> None:
        self.client = client
        self.store = store
        self.state_store = state_store
        self.recovery_keys: list[str] = []
        type(self).instances.append(self)

    async def load(self) -> None:
        self.account = SimpleNamespace(shared=False)

    async def share_keys(self) -> None:
        return None

    async def verify_with_recovery_key(self, recovery_key: str) -> None:
        self.recovery_keys.append(recovery_key)

    async def decrypt_megolm_event(self, event: Any) -> Any:
        error = type(self).decrypt_error
        if error is not None:
            raise error
        return event


class _FakeApiSession:
    def __init__(self, client: _FakeMatrixClient) -> None:
        self.client = client
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeMatrixClient:
    instances: list[_FakeMatrixClient] = []
    login_error: Exception | None = None
    whoami_error: Exception | None = None
    sync_events: list[dict[str, Any]] = []
    history_events: list[dict[str, Any]] = []
    downloads: dict[str, bytes] = {}
    sync_error: Exception | None = None

    def __init__(
        self,
        *,
        mxid: str,
        device_id: str,
        base_url: str,
        token: str | None,
        state_store: Any,
    ) -> None:
        self.mxid = mxid
        self.device_id = device_id
        self.base_url = base_url
        self.state_store = state_store
        self.sync_store: Any = None
        self.api = SimpleNamespace(
            token=token,
            session=_FakeApiSession(self),
            get_download_url=lambda url, *, authenticated: url,
        )
        self.crypto: Any = None
        self.handler: Any = None
        self.login_calls: list[dict[str, Any]] = []
        self.sync_calls: list[dict[str, Any]] = []
        self.history_calls: list[dict[str, Any]] = []
        self.download_calls: list[str] = []
        type(self).instances.append(self)

    async def login(self, **kwargs: Any) -> Any:
        self.login_calls.append(kwargs)
        error = type(self).login_error
        if error is not None:
            raise error
        self.api.token = "access-token"
        return SimpleNamespace(
            user_id="@ada:example.com",
            device_id="ANGEEDEVICE",
            access_token="access-token",
        )

    async def whoami(self) -> Any:
        error = type(self).whoami_error
        if error is not None:
            raise error
        return SimpleNamespace(user_id=self.mxid, device_id=self.device_id)

    def add_event_handler(self, _event_type: Any, handler: Any, *, wait_sync: bool) -> None:
        assert wait_sync is True
        self.handler = handler

    async def create_filter(self, value: Any) -> str:
        assert value["room"]["timeline"]["lazy_load_members"] is True
        return "lazy-members"

    async def sync(self, **kwargs: Any) -> dict[str, Any]:
        self.sync_calls.append(kwargs)
        error = type(self).sync_error
        if error is not None:
            raise error
        index = len(self.sync_calls) - 1
        if index:
            await asyncio.sleep(0.02)
            return {"next_batch": f"s{index + 1}", "rooms": {"join": {}}}
        return {
            "next_batch": "s1",
            "rooms": {
                "join": {
                    "!room:example.com": {
                        "state": {
                            "events": [
                                {
                                    "type": "m.room.name",
                                    "state_key": "",
                                    "content": {"name": "Matrix Room"},
                                }
                            ]
                        },
                        "timeline": {
                            "prev_batch": "t0",
                            "events": list(type(self).sync_events),
                        },
                    }
                }
            },
        }

    def handle_sync(self, raw: dict[str, Any]) -> list[asyncio.Task[Any]]:
        if self.handler is None:
            return []
        tasks: list[asyncio.Task[Any]] = []
        for room_id, room in raw.get("rooms", {}).get("join", {}).items():
            for event in room.get("timeline", {}).get("events", []):
                if event.get("type") in {"m.room.encrypted", "m.room.message"}:
                    tasks.append(asyncio.create_task(self.handler({**event, "room_id": room_id})))
        return tasks

    async def get_messages(self, room_id: str, **kwargs: Any) -> Any:
        self.history_calls.append({"room_id": room_id, **kwargs})
        return SimpleNamespace(events=list(type(self).history_events))

    async def download_media(self, url: str) -> bytes:
        self.download_calls.append(url)
        return type(self).downloads[url]


@pytest.fixture
def matrix_session_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Install fake mautrix/olm modules before importing the worker session."""

    _FakeMatrixClient.instances.clear()
    _FakeMatrixClient.login_error = None
    _FakeMatrixClient.whoami_error = None
    _FakeMatrixClient.sync_events = []
    _FakeMatrixClient.history_events = []
    _FakeMatrixClient.downloads = {}
    _FakeMatrixClient.sync_error = None
    _FakeDatabase.instances.clear()
    _FakeCryptoStore.instances.clear()
    _FakeOlmMachine.instances.clear()
    _FakeOlmMachine.decrypt_error = None

    modules = {
        name: ModuleType(name)
        for name in (
            "mautrix",
            "mautrix.client",
            "mautrix.client.state_store",
            "mautrix.crypto",
            "mautrix.crypto.attachments",
            "mautrix.crypto.store",
            "mautrix.crypto.store.asyncpg",
            "mautrix.types",
            "mautrix.util",
            "mautrix.util.async_db",
        )
    }
    modules["mautrix.client"].Client = _FakeMatrixClient  # type: ignore[attr-defined]
    modules["mautrix.client.state_store"].FileStateStore = _FakeStateStore  # type: ignore[attr-defined]
    modules["mautrix.crypto"].OlmMachine = _FakeOlmMachine  # type: ignore[attr-defined]
    modules["mautrix.crypto.attachments"].decrypt_attachment = (  # type: ignore[attr-defined]
        lambda content, key, hash, iv: b"decrypted:" + content
    )
    modules["mautrix.crypto.store.asyncpg"].PgCryptoStore = _FakeCryptoStore  # type: ignore[attr-defined]
    modules["mautrix.types"].EventType = SimpleNamespace(ROOM_MESSAGE="m.room.message")  # type: ignore[attr-defined]
    modules["mautrix.types"].LoginType = SimpleNamespace(PASSWORD="m.login.password")  # type: ignore[attr-defined]
    modules["mautrix.types"].PaginationDirection = SimpleNamespace(BACKWARD="b")  # type: ignore[attr-defined]
    modules["mautrix.types"].PresenceState = SimpleNamespace(OFFLINE="offline")  # type: ignore[attr-defined]
    modules["mautrix.types"].RoomEventFilter = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    modules["mautrix.types"].RoomFilter = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    modules["mautrix.types"].StateFilter = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    modules["mautrix.types"].Filter = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    modules["mautrix.util.async_db"].Database = _FakeDatabase  # type: ignore[attr-defined]
    for name, module in modules.items():
        if name in {"mautrix", "mautrix.client", "mautrix.crypto", "mautrix.crypto.store", "mautrix.util"}:
            module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(
        sys.modules,
        "angee.messaging_integrate_matrix.session",
        raising=False,
    )
    return importlib.import_module("angee.messaging_integrate_matrix.session")


@pytest.fixture
def matrix_tables(tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Create concrete messaging tables and isolate Matrix session storage."""

    settings.ANGEE_DATA_DIR = str(tmp_path / "data")
    monkeypatch.setattr("angee.integrate.impl.enqueue_task", lambda *args, **kwargs: None)
    created_models = _create_missing_tables(MATRIX_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(MATRIX_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _matrix_channel(user: Any, *, history_seeded: bool = False) -> Any:
    connect = importlib.import_module("angee.messaging_integrate_matrix.connect")
    with system_context(reason="test.messaging.matrix.vendor.seed"):
        Vendor.objects.get_or_create(slug="matrix", defaults={"display_name": "Matrix"})
    channel = connect.create_matrix_channel(
        user,
        "https://8.8.8.8/",
        "@ada:example.com",
        "durable-login-password",
    )
    if history_seeded:
        with system_context(reason="test.messaging.matrix.history.seed"):
            channel.merge_subscription_state(history_seeded=True)
    return channel


@pytest.mark.django_db(transaction=True)
def test_create_matrix_channel_validates_and_starts_basic_auth(
    matrix_tables: Any,
) -> None:
    """Connect persists the normalized homeserver and selected durable credential."""

    admin = _platform_admin("msg-matrix-connect-admin")
    channel = _matrix_channel(admin)

    with system_context(reason="test.messaging.matrix.connect.verify"):
        channel.refresh_from_db()
        assert channel.vendor.slug == "matrix"
        assert channel.backend_class == "matrix"
        assert channel.display_name == "@ada:example.com"
        assert channel.lifecycle == "connected"
        assert channel.subscription_state["homeserver"] == "https://8.8.8.8"
        assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
        assert channel.credential.kind == CredentialKind.BASIC_AUTH


@pytest.mark.django_db(transaction=True)
def test_create_matrix_channel_rejects_non_http_homeserver_before_persisting(
    matrix_tables: Any,
) -> None:
    """A malformed homeserver fails before a Matrix channel row is created."""

    connect = importlib.import_module("angee.messaging_integrate_matrix.connect")
    admin = _platform_admin("msg-matrix-invalid-url-admin")
    with system_context(reason="test.messaging.matrix.invalid_url.seed"):
        Vendor.objects.create(slug="matrix", display_name="Matrix")
    with pytest.raises(ValueError, match="valid Matrix homeserver URL"):
        connect.create_matrix_channel(
            admin,
            "matrix.example.com",
            "@ada:example.com",
            "durable-login-password",
        )

    assert Channel._base_manager.filter(backend_class="matrix").count() == 0
    assert Credential._base_manager.filter(user=admin, name="Matrix - @ada:example.com").count() == 0


@pytest.mark.parametrize("homeserver", ["http://169.254.169.254", "http://224.0.0.1", "http://0.0.0.0"])
def test_matrix_homeserver_rejects_ssrf_escapes(homeserver: str) -> None:
    """Metadata/link-local/multicast targets are refused even for a self-hosted verb."""

    connect = importlib.import_module("angee.messaging_integrate_matrix.connect")

    with pytest.raises(ValueError, match="metadata, link-local, or multicast"):
        connect.matrix_homeserver_url(homeserver)


@pytest.mark.parametrize("homeserver", ["http://127.0.0.1:8008", "http://192.168.1.10", "http://10.0.0.5:8448"])
def test_matrix_homeserver_allows_self_hosted_private_addresses(homeserver: str) -> None:
    """Self-hosted homeservers on private/loopback networks are permitted (allow_private)."""

    connect = importlib.import_module("angee.messaging_integrate_matrix.connect")

    assert connect.matrix_homeserver_url(homeserver) == homeserver


@pytest.mark.django_db(transaction=True)
def test_create_matrix_channel_reuses_named_credential_on_retry(matrix_tables: Any) -> None:
    """A repeated connect reuses the credential row instead of deadlocking on its name."""

    connect = importlib.import_module("angee.messaging_integrate_matrix.connect")
    admin = _platform_admin("msg-matrix-retry-admin")
    with system_context(reason="test.messaging.matrix.retry.seed"):
        Vendor.objects.create(slug="matrix", display_name="Matrix")

    first = connect.create_matrix_channel(
        admin,
        "https://8.8.8.8/",
        "@ada:example.com",
        "first-password",
    )
    second = connect.create_matrix_channel(
        admin,
        "https://8.8.8.8/",
        "@ada:example.com",
        "second-password",
    )

    credentials = Credential._base_manager.filter(user=admin, name="Matrix - @ada:example.com")
    assert credentials.count() == 1
    assert first.credential_id == second.credential_id == credentials.get().pk
    with system_context(reason="test.messaging.matrix.retry.verify"):
        assert credentials.get().reveal()["password"] == "second-password"


@pytest.mark.django_db(transaction=True)
def test_connect_matrix_channel_mutation_dispatches_to_the_service(matrix_tables: Any) -> None:
    """The Matrix mutation selects a credential and returns the shared Channel."""

    admin = _platform_admin("msg-matrix-graphql-admin")
    with system_context(reason="test.messaging.matrix.graphql.seed"):
        Vendor.objects.create(slug="matrix", display_name="Matrix")
    matrix_schema = importlib.import_module("angee.messaging_integrate_matrix.schema")
    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema, parties_schema, messaging_schema, matrix_schema)
    ]
    schema = GraphQLSchemas(addons).build("console")

    result = execute_schema(
        schema,
        """
        mutation ConnectMatrix($homeserver: String!, $username: String!, $password: String!) {
          connect_matrix_channel(homeserver: $homeserver, username: $username, password: $password) {
            id
            display_name
            backend_class
            lifecycle
          }
        }
        """,
        {
            "homeserver": "https://8.8.8.8/",
            "username": "@ada:example.com",
            "password": "durable-login-password",
        },
        request=_request(admin),
    )

    assert result_data(result)["connect_matrix_channel"] == {
        "id": result_data(result)["connect_matrix_channel"]["id"],
        "display_name": "@ada:example.com",
        "backend_class": "MATRIX",
        "lifecycle": "CONNECTED",
    }


def _wait_until(predicate: Any, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.mark.django_db(transaction=True)
def test_matrix_password_login_recovery_secret_sync_and_bounded_backfill(
    matrix_tables: Any,
    matrix_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full advisory-locked run logs in, verifies, syncs, backfills, and ingests."""

    from angee.integrate import session as live_session_module

    monkeypatch.setattr(live_session_module, "AWAITING_PASSWORD_WAKE_SECONDS", 0.01)
    admin = _platform_admin("msg-matrix-session-admin")
    channel = _matrix_channel(admin)
    _FakeMatrixClient.sync_events = [_event("$live")]
    _FakeMatrixClient.history_events = [_event("$history")]
    stop_event = threading.Event()
    session = matrix_session_module.MatrixSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=stop_event,
    )
    failures: list[BaseException] = []

    def operate() -> None:
        try:
            _wait_until(lambda: session.pairing is PairingState.AWAITING_PASSWORD)
            with system_context(reason="test.messaging.matrix.recovery.submit"):
                operator_channel = Channel.objects.get(pk=channel.pk)
                submit_channel_password(operator_channel, "recovery-secret")
            _wait_until(
                lambda: (
                    Message._base_manager.filter(channel_id=channel.pk).count() == 2
                    and bool(Channel._base_manager.get(pk=channel.pk).subscription_state.get("history_seeded"))
                )
            )
            _wait_until(lambda: bool(_FakeCryptoStore.instances) and _FakeCryptoStore.instances[-1].next_batch == "s1")
            stop_event.set()
        except BaseException as error:  # noqa: BLE001 — surface operator-thread failures.
            failures.append(error)
            stop_event.set()

    operator = threading.Thread(target=operate, daemon=True)
    with system_context(reason="test.messaging.matrix.session.run"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        operator.start()
        outcome = session.run()
        operator.join(timeout=3)
        assert not operator.is_alive()

    assert failures == []
    assert outcome is PairingState.PAIRED
    client = _FakeMatrixClient.instances[-1]
    assert client.login_calls == [
        {
            "identifier": "@ada:example.com",
            "login_type": "m.login.password",
            "password": "durable-login-password",
            "device_name": "Angee",
            "store_access_token": True,
            "update_hs_url": False,
        }
    ]
    assert _FakeOlmMachine.instances[-1].recovery_keys == ["recovery-secret"]
    assert len(client.history_calls) == 1
    assert client.history_calls[0]["limit"] == matrix_session_module.INITIAL_ROOM_LIMIT
    assert len(_FakeMatrixClient.history_events) <= matrix_session_module.INITIAL_HISTORY_LIMIT
    assert {message.external_id for message in Message._base_manager.filter(channel_id=channel.pk)} == {
        "!room:example.com/$live",
        "!room:example.com/$history",
    }
    with system_context(reason="test.messaging.matrix.session.material.verify"):
        material = Credential.objects.get(pk=channel.credential_id).reveal()
        assert material["password"] == "durable-login-password"
        assert "recovery_key" not in material
    assert (session_store_path(channel) / "crypto.db").name == "crypto.db"
    assert _FakeDatabase.instances[-1].url.endswith("/crypto.db")


@pytest.mark.django_db(transaction=True)
def test_matrix_recovery_skip_continues_forward_only_and_history_gate_holds(
    matrix_tables: Any,
    matrix_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping recovery still pairs and a seeded channel performs no backfill."""

    from angee.integrate import session as live_session_module

    monkeypatch.setattr(live_session_module, "AWAITING_PASSWORD_WAKE_SECONDS", 0.01)
    admin = _platform_admin("msg-matrix-skip-admin")
    channel = _matrix_channel(admin, history_seeded=True)
    stop_event = threading.Event()
    session = matrix_session_module.MatrixSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=stop_event,
    )
    failures: list[BaseException] = []

    def operate() -> None:
        try:
            _wait_until(lambda: session.pairing is PairingState.AWAITING_PASSWORD)
            with system_context(reason="test.messaging.matrix.recovery.skip"):
                operator_channel = Channel.objects.get(pk=channel.pk)
                assert operator_channel.live_impl.pairing().can_skip is True
                skip_channel_password(operator_channel)
            _wait_until(lambda: session.pairing is PairingState.PAIRED)
            stop_event.set()
        except BaseException as error:  # noqa: BLE001 — surface operator-thread failures.
            failures.append(error)
            stop_event.set()

    operator = threading.Thread(target=operate, daemon=True)
    with system_context(reason="test.messaging.matrix.skip.run"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        operator.start()
        outcome = session.run()
        operator.join(timeout=3)

    assert failures == []
    assert outcome is PairingState.PAIRED
    assert _FakeOlmMachine.instances[-1].recovery_keys == []
    assert _FakeMatrixClient.instances[-1].history_calls == []
    with system_context(reason="test.messaging.matrix.skip.material.verify"):
        material = Credential.objects.get(pk=channel.credential_id).reveal()
        assert material["password"] == "durable-login-password"
        assert "recovery_key" not in material


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("session_facts", "error_attr", "errcode"),
    [
        ({}, "login_error", "M_FORBIDDEN"),
        (
            {
                "user_id": "@ada:example.com",
                "device_id": "ANGEEDEVICE",
                "access_token": "expired-token",
            },
            "whoami_error",
            "M_UNKNOWN_TOKEN",
        ),
    ],
)
def test_matrix_auth_failures_report_logged_out(
    matrix_tables: Any,
    matrix_session_module: ModuleType,
    session_facts: dict[str, str],
    error_attr: str,
    errcode: str,
) -> None:
    """Password rejection and revoked access tokens use generic logged-out state."""

    admin = _platform_admin(f"msg-matrix-{errcode.lower()}-admin")
    channel = _matrix_channel(admin)
    path = session_store_path(channel) / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if session_facts:
        matrix_session_module._write_session_facts(path, session_facts)
    setattr(_FakeMatrixClient, error_attr, _FakeMatrixError(errcode))
    session = matrix_session_module.MatrixSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.client = session._build_client(path)

    session._connect()

    kinds = [session.events.get_nowait()[0] for _ in range(session.events.qsize())]
    assert kinds == ["logged_out", "disconnected"]


@pytest.mark.django_db(transaction=True)
def test_matrix_mid_session_forbidden_is_retriable_not_logged_out(
    matrix_tables: Any,
    matrix_session_module: ModuleType,
) -> None:
    """A non-auth M_FORBIDDEN retains the crypto store and surfaces a session error."""

    admin = _platform_admin("msg-matrix-mid-forbidden-admin")
    channel = _matrix_channel(admin)
    path = session_store_path(channel) / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    matrix_session_module._write_session_facts(
        path,
        {
            "user_id": "@ada:example.com",
            "device_id": "ANGEEDEVICE",
            "access_token": "access-token",
            "pickle_key": "pickle-key",
            "recovery": "skipped",
        },
    )
    _FakeMatrixClient.sync_error = _FakeMatrixError("M_FORBIDDEN")
    session = matrix_session_module.MatrixSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.client = session._build_client(path)

    session._connect()

    kinds = [session.events.get_nowait()[0] for _ in range(session.events.qsize())]
    assert kinds == ["paired", "disconnected"]
    assert isinstance(session.outcome_error, _FakeMatrixError)
    assert session.outcome_error.errcode == "M_FORBIDDEN"


@pytest.mark.django_db(transaction=True)
def test_matrix_undecryptable_event_is_counted_and_skipped(
    matrix_tables: Any,
    matrix_session_module: ModuleType,
) -> None:
    """Missing Megolm keys skip one event without taking down history or sync."""

    admin = _platform_admin("msg-matrix-undecryptable-admin")
    channel = _matrix_channel(admin)
    session = matrix_session_module.MatrixSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    path = session_store_path(channel) / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    session.client = session._build_client(path)

    class _MissingMegolmSession:
        async def decrypt_megolm_event(self, _event: Any) -> Any:
            raise ValueError("missing inbound group session")

    session.client.crypto = _MissingMegolmSession()
    encrypted = {
        "type": "m.room.encrypted",
        "room_id": "!room:example.com",
        "event_id": "$encrypted",
        "sender": "@grace:example.com",
        "content": {},
    }

    assert asyncio.run(session._queued_event(encrypted)) is None
    assert session._undecryptable_events == 1


@pytest.mark.django_db(transaction=True)
def test_matrix_download_authenticates_and_decrypts_encrypted_attachment(
    matrix_tables: Any,
    matrix_session_module: ModuleType,
) -> None:
    """Media resolution stays on the owning Matrix loop and decrypts after download."""

    admin = _platform_admin("msg-matrix-media-admin")
    channel = _matrix_channel(admin)
    session = matrix_session_module.MatrixSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    path = session_store_path(channel) / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    matrix_session_module._write_session_facts(
        path,
        {
            "user_id": "@ada:example.com",
            "device_id": "ANGEEDEVICE",
            "access_token": "media-token",
        },
    )
    session.client = session._build_client(path)
    _FakeMatrixClient.downloads = {"mxc://example.com/encrypted": b"ciphertext"}
    loop = asyncio.new_event_loop()
    session._loop = loop
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        content = session._download(
            None,
            MatrixMediaFact(
                url="mxc://example.com/encrypted",
                key="key",
                hash="hash",
                iv="iv",
            ),
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1)
        loop.close()

    assert content == b"decrypted:ciphertext"
    assert session.client.download_calls == ["mxc://example.com/encrypted"]


@pytest.mark.django_db(transaction=True)
def test_matrix_reset_wipes_only_recovery_key(
    matrix_tables: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backend declaration preserves the durable BASIC_AUTH password on reset."""

    from angee.messaging import connect as messaging_connect

    admin = _platform_admin("msg-matrix-reset-admin")
    channel = _matrix_channel(admin)
    channel.credential.update_material(recovery_key="transient-recovery-key")
    monkeypatch.setattr(messaging_connect, "await_session_exit", lambda _channel: None)
    monkeypatch.setattr(messaging_connect, "reset_session_store", lambda _channel: None)
    monkeypatch.setattr(messaging_connect, "resume_channel_pairing", lambda _channel: None)

    messaging_connect.reset_channel_pairing(channel)

    with system_context(reason="test.messaging.matrix.reset.verify"):
        material = Credential.objects.get(pk=channel.credential_id).reveal()
        assert material == {
            "username": "@ada:example.com",
            "password": "durable-login-password",
        }
