"""Tests for the Telegram channel addon without importing the vendor SDK."""

from __future__ import annotations

import asyncio
import importlib
import queue
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind, handler_for
from angee.integrate.live import PairingState, session_store_path
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.models import IntegrationRuntimeStatus
from angee.integrate.sync import BridgeProgressReporter
from angee.messaging.connect import submit_channel_password
from angee.messaging.models import Thread
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

TELEGRAM_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel)

Credential = apps.get_model("integrate", "Credential")


def _identity() -> ModuleType:
    """Import the console-safe identity module, failing as an unmet feature."""

    try:
        return importlib.import_module("angee.messaging_integrate_telegram.identity")
    except ModuleNotFoundError:
        pytest.fail("The Telegram identity module is not implemented.")


def _telegram_module(name: str) -> ModuleType:
    """Import one Telegram addon module, failing as an unmet feature."""

    try:
        return importlib.import_module(f"angee.messaging_integrate_telegram.{name}")
    except ModuleNotFoundError:
        pytest.fail(f"The Telegram {name} module is not implemented.")


@pytest.fixture
def telegram_tables(
    tmp_path: Any,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Create concrete messaging tables and isolate Telegram session storage."""

    settings.ANGEE_DATA_DIR = str(tmp_path / "data")
    monkeypatch.setattr("angee.integrate.impl.enqueue_task", lambda *args, **kwargs: None)
    created_models = _create_missing_tables(TELEGRAM_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(TELEGRAM_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _message(**fields: Any) -> SimpleNamespace:
    """Return a Telethon-shaped message without importing Telethon."""

    defaults = {
        "id": 17,
        "date": datetime(2026, 7, 16, 8, 30, tzinfo=timezone.utc),
        "raw_text": "Hello from Telegram",
        "out": False,
        "reply_to_msg_id": None,
        "media": None,
        "file": None,
    }
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def test_telegram_external_ids_are_chat_scoped() -> None:
    """Telegram message ids repeat across chats, so the chat scopes ingest identity."""

    identity = _identity()

    assert identity.external_id(-10042, 17) == "-10042/17"
    assert identity.external_id(91, 17) == "91/17"


def test_telegram_handle_keeps_the_stable_user_id() -> None:
    """Changing profile labels never changes the Telegram handle identity."""

    identity = _identity()
    user = SimpleNamespace(
        id=4321,
        phone="420777123456",
        username="ada",
        first_name="Ada",
        last_name="Lovelace",
    )

    handle = identity.handle_for_peer(user)

    assert handle.platform == "telegram"
    assert handle.external_id == "4321"
    assert handle.value == "+420777123456"
    assert handle.display_name == "Ada Lovelace"


@pytest.mark.parametrize(
    ("phone", "username", "expected"),
    [
        ("420777123456", "ada", "+420777123456"),
        ("", "ada", "@ada"),
        ("", "", "4321"),
    ],
)
def test_telegram_account_label_preference_order(phone: str, username: str, expected: str) -> None:
    """Phone, username, then stable id is the one account-label preference order."""

    identity = _identity()

    assert identity.account_label("4321", phone=phone, username=username) == expected


@pytest.mark.parametrize(
    ("is_private", "is_group", "is_channel", "expected"),
    [
        (True, False, False, "direct"),
        (False, True, False, "group"),
        (False, True, True, "group"),
        (False, False, True, "public_thread"),
        (False, False, False, "group"),
    ],
)
def test_telegram_thread_modality(
    is_private: bool,
    is_group: bool,
    is_channel: bool,
    expected: str,
) -> None:
    """Megagroups remain groups; a broadcast channel maps onto the public-thread shape."""

    identity = _identity()

    assert (
        identity.thread_modality(
            is_private=is_private,
            is_group=is_group,
            is_channel=is_channel,
        )
        == expected
    )
    # The vocabulary belongs to Thread, not to Telegram: modality is a real enum
    # column, so a value the owner rejects fails the whole account's ingest — not
    # just the one chat. Pinning the string alone is what let `channel` ship.
    assert expected in Thread.Modality.values


def test_telegram_message_maps_directly_to_the_neutral_ingest_shape() -> None:
    """The sole Telegram producer emits ParsedMessage without a vendor DTO."""

    identity = _identity()
    sender = SimpleNamespace(
        id=4321,
        phone="",
        username="ada",
        first_name="Ada",
        last_name="Lovelace",
    )
    chat = SimpleNamespace(id=4321, title="Ada Lovelace")

    parsed = identity.parsed_message(
        _message(reply_to_msg_id=12),
        chat_id=4321,
        sender_id=4321,
        sender=sender,
        chat=chat,
        is_private=True,
        is_group=False,
        is_channel=False,
    )

    assert parsed.external_id == "4321/17"
    assert parsed.platform == "telegram"
    assert parsed.direction == "inbound"
    assert parsed.sender is not None and parsed.sender.external_id == "4321"
    assert parsed.sent_at == datetime(2026, 7, 16, 8, 30, tzinfo=timezone.utc)
    assert parsed.in_reply_to == "4321/12"
    assert parsed.thread is not None
    assert parsed.thread.external_id == "4321"
    assert parsed.thread.modality == "direct"
    assert parsed.thread.title == "Ada Lovelace"
    assert parsed.body is not None and parsed.body.text == "Hello from Telegram"
    assert parsed.metadata["chat_id"] == "4321"
    assert parsed.metadata["message_id"] == 17


def test_telegram_message_exposes_media_for_task_thread_download() -> None:
    """The vendor thread queues media facts and the raw message, never bytes or DB writes."""

    identity = _identity()
    wire = _message(
        raw_text="photo caption",
        media=object(),
        file=SimpleNamespace(mime_type="image/jpeg", name="photo.jpg"),
    )

    parsed = identity.parsed_message(
        wire,
        chat_id=-10042,
        sender_id=4321,
        sender=SimpleNamespace(id=4321, username="ada"),
        chat=SimpleNamespace(id=42, title="Angee Group"),
        is_private=False,
        is_group=True,
        is_channel=True,
    )

    assert parsed.thread is not None and parsed.thread.modality == "group"
    assert parsed.body is not None and parsed.body.text == "photo caption"
    assert parsed.metadata["_media_facts"] == (identity.MediaItem(mime="image/jpeg", name="photo.jpg"),)


def test_app_keys_credential_kind_owns_the_application_pair() -> None:
    """The closed credential vocabulary exposes reusable application keys."""

    app_keys = CredentialKind("app_keys")
    assert app_keys.value == "app_keys"
    assert app_keys.label == "App Keys"
    handler = handler_for(app_keys)
    assert handler.material_field == "app_secret"
    assert handler.input_material_fields() == ("app_id", "app_secret")
    with pytest.raises(ValueError, match="app_id and app_secret"):
        handler.validate({"app_id": "", "app_secret": "secret"})
    with pytest.raises(ValueError, match="app_id and app_secret"):
        handler.validate({"app_id": "123456", "app_secret": ""})
    assert handler.auth_headers(SimpleNamespace()) == {}


def _app_keys_credential(
    user: Any,
    *,
    app_id: str = "123456",
    app_secret: str = "telegram-api-hash",
    name: str = "Telegram application",
) -> Any:
    """Create one app-keys credential the way integrate's create mutation does."""

    with system_context(reason="test.messaging.telegram.credential.seed"):
        return Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.APP_KEYS,
            name=name,
            material={"app_id": app_id, "app_secret": app_secret},
        )


@pytest.mark.django_db(transaction=True)
def test_create_telegram_channel_shares_one_selected_app_keys_credential(
    telegram_tables: Any,
) -> None:
    """Channels select an app-key credential; one registration serves many accounts."""

    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-connect-admin")
    with system_context(reason="test.messaging.telegram.vendor.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    credential = _app_keys_credential(admin)

    channel = connect.create_telegram_channel(admin, name="Ada Telegram", credential=credential)
    second = connect.create_telegram_channel(admin, name="Work Telegram", credential=credential)

    with system_context(reason="test.messaging.telegram.connect.verify"):
        channel.refresh_from_db()
        assert channel.owner_id == admin.pk
        assert channel.created_by_id == admin.pk
        assert channel.vendor.slug == "telegram"
        assert channel.backend_class == "telegram"
        assert channel.lifecycle == "connected"
        assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
        # Telegram's api_id identifies the application, not a phone number, so a
        # second account reuses the same registration instead of copying it.
        assert channel.credential_id == credential.pk
        assert second.credential_id == credential.pk
        assert Credential._base_manager.count() == 1


@pytest.mark.django_db(transaction=True)
def test_create_telegram_channel_rejects_an_invalid_app_id_before_persisting(
    telegram_tables: Any,
) -> None:
    """A malformed api_id fails before the channel row exists, not at pair time."""

    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-invalid-api-id-admin")
    with system_context(reason="test.messaging.telegram.invalid_api_id.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    credential = _app_keys_credential(admin, app_id="not-an-integer")

    with pytest.raises(ValueError, match="invalid app_id"):
        connect.create_telegram_channel(admin, name="Invalid Telegram", credential=credential)

    with system_context(reason="test.messaging.telegram.invalid_api_id.verify"):
        assert Channel._base_manager.filter(display_name="Invalid Telegram").count() == 0


@pytest.mark.django_db(transaction=True)
def test_create_telegram_channel_rejects_a_credential_of_another_kind(
    telegram_tables: Any,
) -> None:
    """Only an application registration can parameterize a Telegram client."""

    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-wrong-kind-admin")
    with system_context(reason="test.messaging.telegram.wrong_kind.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
        token = Credential.objects.create_local_credential(
            admin,
            kind=CredentialKind.STATIC_TOKEN,
            name="Not application keys",
            material={"api_key": "nope"},
        )

    with pytest.raises(ValueError, match="app-keys credential"):
        connect.create_telegram_channel(admin, name="Wrong Kind", credential=token)

    with system_context(reason="test.messaging.telegram.wrong_kind.verify"):
        assert Channel._base_manager.filter(display_name="Wrong Kind").count() == 0


@pytest.mark.django_db(transaction=True)
def test_telegram_backend_registration_and_pairing_projection(telegram_tables: Any) -> None:
    """Autoconfig registers a live backend whose projection prefers saved profile labels."""

    autoconfig = _telegram_module("autoconfig")
    backend_module = _telegram_module("backend")
    assert autoconfig.SETTINGS == {
        "ANGEE_CHANNEL_BACKEND_CLASSES.telegram": ("angee.messaging_integrate_telegram.backend.TelegramChannelBackend"),
        "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.telegram_takeout": (
            "angee.messaging_integrate_telegram.extractor.TelegramTakeoutExtractor"
        ),
    }
    assert backend_module.TelegramChannelBackend.session_queue == "telegram"

    admin = _platform_admin("msg-telegram-pairing-admin")
    with system_context(reason="test.messaging.telegram.pairing.seed"):
        vendor = Vendor.objects.create(slug="telegram", display_name="Telegram")
        channel = Channel.objects.create(
            vendor=vendor,
            owner=admin,
            backend_class="telegram",
            display_name="Telegram",
            lifecycle="connected",
            created_by_id=admin.pk,
            subscription_state={
                "desired": Channel.LiveState.LIVE,
                "own_id": "4321",
                "phone": "420777123456",
                "username": "ada",
            },
        )

    pairing = channel.backend.pairing()

    assert pairing.state.value == "paired"
    assert pairing.own_id == "4321"
    assert pairing.account_label == "+420777123456"
    assert channel.backend.normalize_account_id(" 4321 ") == "4321"


@pytest.mark.django_db(transaction=True)
def test_connect_telegram_channel_mutation_dispatches_to_the_service(telegram_tables: Any) -> None:
    """The vendor mutation selects a credential by id and returns the shared Channel."""

    admin = _platform_admin("msg-telegram-graphql-admin")
    with system_context(reason="test.messaging.telegram.graphql.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    credential = _app_keys_credential(admin)
    telegram_schema = _telegram_module("schema")
    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (
            iam_schema,
            integrate_schema,
            parties_schema,
            messaging_schema,
            telegram_schema,
        )
    ]
    schema = GraphQLSchemas(addons).build("console")

    result = execute_schema(
        schema,
        """
        mutation ConnectTelegram($name: String!, $credentialId: ID!) {
          connect_telegram_channel(name: $name, credential_id: $credentialId) {
            id
            display_name
            backend_class
            lifecycle
          }
        }
        """,
        {
            "name": "Ada Telegram",
            "credentialId": str(credential.sqid),
        },
        request=_request(admin),
    )

    assert result_data(result)["connect_telegram_channel"] == {
        "id": result_data(result)["connect_telegram_channel"]["id"],
        "display_name": "Ada Telegram",
        "backend_class": "TELEGRAM",
        "lifecycle": "CONNECTED",
    }


class _FakeSessionPasswordNeededError(Exception):
    """Fake of Telethon's two-step verification signal."""


class _FakePasswordHashInvalidError(Exception):
    """Fake of Telethon rejecting an incorrect two-step password."""


class _FakeUnauthorizedError(Exception):
    """Fake of Telethon's unauthorized RPC error base."""


class _FakeAuthKeyUnregisteredError(_FakeUnauthorizedError):
    """Fake of a phone-side revoked/unregistered authorization key."""


class _FakeAuthKeyInvalidError(_FakeUnauthorizedError):
    """Fake of an invalid persisted authorization key."""


class _FakeAuthKeyPermEmptyError(_FakeUnauthorizedError):
    """Fake of an authorization key missing permanent permission."""


class _FakeSessionRevokedError(_FakeUnauthorizedError):
    """Fake of Telethon's explicit revoked-session signal."""


class _FakeNewMessage:
    """Fake event builder accepted by ``add_event_handler``."""


class _FakeQrLogin:
    """Rotate once, then request two-step verification."""

    def __init__(self) -> None:
        self.index = 0
        self.recreate_calls = 0
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=30)

    @property
    def url(self) -> str:
        return ("tg://login?token=first", "tg://login?token=second")[self.index]

    async def wait(self, timeout: float | None = None) -> Any:
        assert timeout is not None
        if self.index == 0:
            self.expires = datetime.now(timezone.utc) - timedelta(seconds=1)
            raise TimeoutError
        raise _FakeSessionPasswordNeededError

    async def recreate(self) -> None:
        self.recreate_calls += 1
        self.index = 1
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=30)


class _FakeTelegramClient:
    """Async Telethon boundary used by the worker-session regression."""

    instances: list[_FakeTelegramClient] = []

    def __init__(self, session: str, api_id: int, api_hash: str) -> None:
        self.session = session
        self.api_id = api_id
        self.api_hash = api_hash
        self.qr = _FakeQrLogin()
        self.passwords: list[str] = []
        self.handler: Any = None
        self.disconnected: asyncio.Future[None] | None = None
        type(self).instances.append(self)

    def add_event_handler(self, handler: Any, event: Any) -> None:
        assert isinstance(event, _FakeNewMessage)
        self.handler = handler

    async def connect(self) -> None:
        self.disconnected = asyncio.get_running_loop().create_future()

    async def is_user_authorized(self) -> bool:
        return False

    async def qr_login(self) -> _FakeQrLogin:
        return self.qr

    async def sign_in(self, *, password: str) -> SimpleNamespace:
        self.passwords.append(password)
        if password == "wrong-password":
            raise _FakePasswordHashInvalidError
        loop = asyncio.get_running_loop()
        assert self.disconnected is not None
        loop.call_later(0.2, self.disconnected.set_result, None)
        return SimpleNamespace(
            id=4321,
            phone="420777123456",
            username="ada",
            first_name="Ada",
            last_name="Lovelace",
        )

    async def get_dialogs(self, limit: int) -> list[Any]:
        assert limit > 0
        return []

    def iter_messages(self, entity: Any, limit: int, *, reverse: bool) -> Any:
        del entity, limit, reverse
        raise AssertionError("No dialogs means no history iterator call.")

    async def download_media(self, payload: Any, file: type[bytes]) -> bytes:
        assert file is bytes
        return bytes(payload)

    async def disconnect(self) -> None:
        if self.disconnected is not None and not self.disconnected.done():
            self.disconnected.set_result(None)


class _ConnectFailureClient(_FakeTelegramClient):
    """Fake whose vendor connect fails before authorization."""

    async def connect(self) -> None:
        raise RuntimeError("Telegram application credentials were rejected.")


class _RevokedAuthClient(_FakeTelegramClient):
    """Fake whose persisted authorization key was revoked remotely."""

    async def connect(self) -> None:
        raise _FakeAuthKeyUnregisteredError("AUTH_KEY_UNREGISTERED")


class _UnauthorizedExistingClient(_FakeTelegramClient):
    """Fake whose existing store connects but no longer authorizes a user."""

    async def qr_login(self) -> _FakeQrLogin:
        raise AssertionError("An existing unauthorized store must log out, not re-pair.")


@pytest.fixture
def telegram_session_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Load against fake Telethon, then remove the fake-bound worker module."""

    telethon = ModuleType("telethon")
    telethon.TelegramClient = _FakeTelegramClient  # type: ignore[attr-defined]
    telethon.events = SimpleNamespace(NewMessage=_FakeNewMessage)  # type: ignore[attr-defined]
    errors = ModuleType("telethon.errors")
    errors.SessionPasswordNeededError = _FakeSessionPasswordNeededError  # type: ignore[attr-defined]
    errors.PasswordHashInvalidError = _FakePasswordHashInvalidError  # type: ignore[attr-defined]
    errors.UnauthorizedError = _FakeUnauthorizedError  # type: ignore[attr-defined]
    errors.AuthKeyUnregisteredError = _FakeAuthKeyUnregisteredError  # type: ignore[attr-defined]
    errors.AuthKeyInvalidError = _FakeAuthKeyInvalidError  # type: ignore[attr-defined]
    errors.AuthKeyPermEmptyError = _FakeAuthKeyPermEmptyError  # type: ignore[attr-defined]
    errors.SessionRevokedError = _FakeSessionRevokedError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "telethon", telethon)
    monkeypatch.setitem(sys.modules, "telethon.errors", errors)
    module_name = "angee.messaging_integrate_telegram.session"
    sys.modules.pop(module_name, None)
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        pytest.fail("The Telegram worker session is not implemented.")
    try:
        yield module
    finally:
        sys.modules.pop(module_name, None)


def _wait_until(predicate: Any, *, timeout: float = 2.0) -> None:
    """Wait for a cross-thread session state without importing vendor code."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.mark.django_db(transaction=True)
def test_telegram_session_rotates_qr_accepts_password_and_claims_account(
    telegram_tables: Any,
    telegram_session_module: ModuleType,
) -> None:
    """Timeout rotation → bounded password handoff → generic paired account claim."""

    session_module = telegram_session_module
    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-session-admin")
    with system_context(reason="test.messaging.telegram.session.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    channel = connect.create_telegram_channel(
        admin,
        name="Ada Telegram",
        credential=_app_keys_credential(admin),
    )
    reports: list[dict[str, Any]] = []
    real_reporter = BridgeProgressReporter(channel)

    class _Reporter:
        def report(self, stage: Any, **payload: Any) -> None:
            reports.append({"stage": stage, **payload})
            real_reporter.report(stage, **payload)

    session = session_module.TelegramSession(
        channel,
        reporter=_Reporter(),
        stop_event=threading.Event(),
    )
    submitted = "correct-horse-battery-staple"

    def _submit_password() -> None:
        _wait_until(lambda: session.pairing is PairingState.AWAITING_PASSWORD)
        with system_context(reason="test.messaging.telegram.session.password"):
            operator_channel = Channel.objects.get(pk=channel.pk)
            submit_channel_password(operator_channel, "wrong-password")
        _wait_until(
            lambda: any(
                report.get("details", {}).get("pairing", {}).get("message")
                == "Incorrect Telegram two-step verification password. Try again."
                for report in reports
            )
        )
        with system_context(reason="test.messaging.telegram.session.password.retry"):
            operator_channel = Channel.objects.get(pk=channel.pk)
            submit_channel_password(operator_channel, submitted)

    operator = threading.Thread(target=_submit_password, daemon=True)
    _FakeTelegramClient.instances.clear()
    operator.start()
    with system_context(reason="test.messaging.telegram.session.run"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        outcome = session.run()
    operator.join(timeout=1)

    assert not operator.is_alive()
    assert outcome is PairingState.PAIRED
    client = _FakeTelegramClient.instances[-1]
    assert client.session.endswith("/telegram/" + str(channel.sqid) + "/session")
    assert client.api_id == 123456
    assert client.api_hash == "telegram-api-hash"
    assert client.qr.recreate_calls == 1
    assert client.passwords == ["wrong-password", submitted]
    awaiting_scan = [
        report["details"]["pairing"]
        for report in reports
        if report.get("details", {}).get("pairing", {}).get("state") == PairingState.AWAITING_SCAN
    ]
    assert len(awaiting_scan) == 2
    assert awaiting_scan[0]["qr"] != awaiting_scan[1]["qr"]
    with system_context(reason="test.messaging.telegram.session.verify"):
        channel.refresh_from_db()
        assert channel.subscription_state["own_id"] == "4321"
        assert channel.subscription_state["phone"] == "420777123456"
        assert channel.backend.pairing().account_label == "+420777123456"
        assert "password" not in channel.credential.reveal()

    asyncio.run(
        client.handler(
            SimpleNamespace(
                message=_message(
                    id=18,
                    chat_id=4321,
                    sender_id=4321,
                    sender=SimpleNamespace(id=4321, username="ada"),
                    chat=SimpleNamespace(id=4321, first_name="Ada", last_name="Lovelace"),
                    is_private=True,
                    is_group=False,
                    is_channel=False,
                ),
                chat_id=4321,
                chat=SimpleNamespace(id=4321, first_name="Ada", last_name="Lovelace"),
                is_private=True,
                is_group=False,
                is_channel=False,
            )
        )
    )
    kind, batch = session.events.get_nowait()
    assert kind == "messages"
    assert batch[0][0].external_id == "4321/18"


@pytest.mark.django_db(transaction=True)
def test_telegram_connect_failure_latches_runtime_error_and_stops_redispatch(
    telegram_tables: Any,
    telegram_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vendor-loop crash is recorded on the task thread and gates the reconciler."""

    from angee.integrate import tasks as tasks_module

    session_module = telegram_session_module
    monkeypatch.setattr(tasks_module, "bridge_models", lambda _base: (Channel,))
    monkeypatch.setattr(session_module.TelegramSession, "client_class", _ConnectFailureClient)
    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-connect-failure-admin")
    with system_context(reason="test.messaging.telegram.connect_failure.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    channel = connect.create_telegram_channel(
        admin,
        name="Broken Telegram",
        credential=_app_keys_credential(admin, app_secret="wrong-api-hash"),
    )

    result = tasks_module.run_bridge_session(channel._meta.label_lower, channel.pk)

    assert result["ok"] is False
    assert result["session_error"] is True
    channel.refresh_from_db()
    assert channel.runtime_status == IntegrationRuntimeStatus.ERROR
    assert channel.sync_stage == Channel.SyncStage.FAILED
    assert "application credentials were rejected" in channel.sync_error.lower()
    assert tasks_module.ensure_bridge_sessions() == {"ok": True, "dispatched": 0}


@pytest.mark.parametrize(
    ("client_class", "existing_store"),
    [
        (_RevokedAuthClient, False),
        (_UnauthorizedExistingClient, True),
    ],
)
@pytest.mark.django_db(transaction=True)
def test_telegram_revoked_authorization_reaches_logged_out_release(
    telegram_tables: Any,
    telegram_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    client_class: type[_FakeTelegramClient],
    existing_store: bool,
) -> None:
    """Revoked auth errors and unauthorized retained stores use generic logout."""

    from angee.integrate import tasks as tasks_module

    session_module = telegram_session_module
    monkeypatch.setattr(session_module.TelegramSession, "client_class", client_class)
    connect = _telegram_module("connect")
    admin = _platform_admin(f"msg-telegram-revoked-{client_class.__name__}")
    with system_context(reason="test.messaging.telegram.revoked.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    channel = connect.create_telegram_channel(
        admin,
        name="Revoked Telegram",
        credential=_app_keys_credential(admin),
    )
    if existing_store:
        store = session_store_path(channel)
        store.mkdir(parents=True, exist_ok=True)
        (store / "session.session").write_bytes(b"revoked-session")

    result = tasks_module.run_bridge_session(channel._meta.label_lower, channel.pk)

    assert result == {"ok": False, "logged_out": True}
    channel.refresh_from_db()
    assert channel.runtime_status == IntegrationRuntimeStatus.ERROR
    assert channel.subscription_state["desired"] == Channel.LiveState.STOPPED
    assert channel.backend.pairing().state is PairingState.LOGGED_OUT


@pytest.mark.django_db(transaction=True)
def test_telegram_password_wait_runs_off_the_vendor_event_loop(
    telegram_tables: Any,
    telegram_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The synchronous operator queue poll runs in a worker thread, not asyncio."""

    session_module = telegram_session_module
    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-password-loop-admin")
    with system_context(reason="test.messaging.telegram.password_loop.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    channel = connect.create_telegram_channel(
        admin,
        name="Password Telegram",
        credential=_app_keys_credential(admin),
    )
    session = session_module.TelegramSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    request_threads: list[int | None] = []

    def request_password(message: str = "") -> str:
        assert message
        request_threads.append(threading.current_thread().ident)
        return "secret"

    async def sign_in(*, password: str) -> SimpleNamespace:
        assert password == "secret"
        return SimpleNamespace(id=4321)

    monkeypatch.setattr(session, "request_password", request_password)
    session.client = SimpleNamespace(sign_in=sign_in)

    assert asyncio.run(session._sign_in_with_password()).id == 4321
    assert request_threads
    assert request_threads[0] != threading.current_thread().ident


def test_telegram_disconnect_wait_reuses_one_vendor_future(
    telegram_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One connect epoch reads Telethon's shield-producing property exactly once."""

    session_module = telegram_session_module
    monkeypatch.setattr(session_module, "CONNECTION_WAKE_SECONDS", 0.01)
    session = session_module.TelegramSession.__new__(session_module.TelegramSession)
    session._stopping = threading.Event()
    session.stop_event = threading.Event()

    class CountingClient:
        accesses = 0

        def __init__(self) -> None:
            self.future: asyncio.Future[None] | None = None

        @property
        def disconnected(self) -> asyncio.Future[None]:
            type(self).accesses += 1
            assert self.future is not None
            return asyncio.shield(self.future)

    async def wait_for_stop() -> None:
        client = CountingClient()
        client.future = asyncio.get_running_loop().create_future()
        session.client = client
        asyncio.get_running_loop().call_later(0.035, session._stopping.set)
        await session._wait_until_disconnected()

    asyncio.run(wait_for_stop())
    assert CountingClient.accesses == 1


@pytest.mark.django_db(transaction=True)
def test_telegram_qr_rotation_cap_reports_stopped_and_exits(
    telegram_tables: Any,
    telegram_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expired QR rounds stop at the cap and leave an operator-visible state."""

    session_module = telegram_session_module
    monkeypatch.setattr(session_module, "QR_ROTATION_LIMIT", 2, raising=False)
    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-qr-cap-admin")
    with system_context(reason="test.messaging.telegram.qr_cap.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    channel = connect.create_telegram_channel(
        admin,
        name="QR Cap Telegram",
        credential=_app_keys_credential(admin),
    )

    class ExpiringQr:
        recreate_calls = 0
        url = "tg://login?token=expired"
        expires = datetime.now(timezone.utc) - timedelta(seconds=1)

        async def wait(self, timeout: float | None = None) -> Any:
            assert timeout is not None and timeout <= session_module.QR_WAIT_SECONDS
            raise TimeoutError

        async def recreate(self) -> None:
            self.recreate_calls += 1
            if self.recreate_calls >= 2:
                raise AssertionError("QR rotation exceeded its configured cap.")
            self.expires = datetime.now(timezone.utc) - timedelta(seconds=1)

    qr = ExpiringQr()

    async def qr_login() -> ExpiringQr:
        return qr

    session = session_module.TelegramSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.client = SimpleNamespace(qr_login=qr_login)

    assert asyncio.run(session._pair()) is None
    with system_context(reason="test.messaging.telegram.qr_cap.drain"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        assert session._drain_once() is True
        assert session._drain_once() is True
        assert session._drain_once() is False
    assert qr.recreate_calls == 1
    assert session.pairing is PairingState.STOPPED
    channel.refresh_from_db()
    assert channel.sync_progress["details"]["pairing"]["state"] == PairingState.STOPPED


def test_telegram_initial_history_shares_the_global_budget_across_dialogs(
    telegram_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No newest dialog may consume more than its share of the bounded seed."""

    session_module = telegram_session_module
    monkeypatch.setattr(session_module, "INITIAL_HISTORY_LIMIT", 10)
    monkeypatch.setattr(session_module, "INITIAL_CONVERSATION_LIMIT", 2)
    session = session_module.TelegramSession.__new__(session_module.TelegramSession)
    session.bridge = SimpleNamespace(sqid="chn_budget")
    session._stopping = threading.Event()
    session.stop_event = threading.Event()
    session.events = queue.Queue()
    limits: list[int] = []

    class HistoryClient:
        async def get_dialogs(self, limit: int) -> list[Any]:
            assert limit == 2
            return [SimpleNamespace(entity="one"), SimpleNamespace(entity="two")]

        def iter_messages(self, entity: Any, limit: int, *, reverse: bool) -> Any:
            del entity, reverse
            limits.append(limit)

            async def empty() -> Any:
                if False:
                    yield None

            return empty()

    session.client = HistoryClient()

    assert asyncio.run(session._initial_history()) is True
    assert limits == [5, 5]


@pytest.mark.django_db(transaction=True)
def test_telegram_authorized_session_retries_history_until_seeded_once(
    telegram_tables: Any,
    telegram_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted first seed retries after reconnect and records completion once."""

    session_module = telegram_session_module
    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-history-seed-admin")
    with system_context(reason="test.messaging.telegram.history_seed.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    channel = connect.create_telegram_channel(
        admin,
        name="History Telegram",
        credential=_app_keys_credential(admin),
    )
    session = session_module.TelegramSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    disconnected: asyncio.Future[None] | None = None

    class AuthorizedClient:
        async def connect(self) -> None:
            nonlocal disconnected
            disconnected = asyncio.get_running_loop().create_future()
            disconnected.set_result(None)
            self.disconnected = disconnected

        async def is_user_authorized(self) -> bool:
            return True

        async def get_me(self) -> SimpleNamespace:
            return SimpleNamespace(id=4321, phone="", username="ada")

    session.client = AuthorizedClient()
    seed_calls: list[str] = []

    async def initial_history() -> bool:
        seed_calls.append("seed")
        return True

    monkeypatch.setattr(session, "_initial_history", initial_history)
    asyncio.run(session._run_client())

    queued = list(session.events.queue)
    assert seed_calls == ["seed"]
    assert ("history_seeded", None) in queued
    with (
        system_context(reason="test.messaging.telegram.history_seed.record"),
        bridge_advisory_lock(channel) as acquired,
    ):
        assert acquired
        assert session._handle("history_seeded", None) is True
    channel.refresh_from_db()
    assert channel.subscription_state["history_seeded"] is True


@pytest.mark.django_db(transaction=True)
def test_telegram_initial_history_lands_caption_and_media_marker_through_ingest(
    telegram_tables: Any,
    telegram_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bounded seed uses the normal chunk, resolution, download, and ingest path."""

    session_module = telegram_session_module
    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-history-ingest-admin")
    with system_context(reason="test.messaging.telegram.history_ingest.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    channel = connect.create_telegram_channel(
        admin,
        name="History Ingest Telegram",
        credential=_app_keys_credential(admin),
    )
    wire = _message(
        id=44,
        chat_id=-10042,
        sender_id=4321,
        sender=SimpleNamespace(id=4321, username="ada"),
        raw_text="history caption",
        media=object(),
        file=SimpleNamespace(mime_type="image/jpeg", name="history.jpg"),
    )
    dialog = SimpleNamespace(entity=SimpleNamespace(id=-10042, title="History Group"))

    class HistoryClient:
        async def get_dialogs(self, limit: int) -> list[Any]:
            assert limit > 0
            return [dialog]

        def iter_messages(self, entity: Any, limit: int, *, reverse: bool) -> Any:
            assert entity is dialog.entity
            assert limit > 0
            assert reverse is False

            async def messages() -> Any:
                yield wire

            return messages()

    session = session_module.TelegramSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.client = HistoryClient()
    downloads: list[Any] = []

    def download(payload: Any, _fact: Any) -> None:
        downloads.append(payload)

    monkeypatch.setattr(session, "_download", download)

    assert asyncio.run(session._initial_history()) is True
    kind, batch = session.events.get_nowait()
    assert kind == "messages"
    with (
        system_context(reason="test.messaging.telegram.history_ingest.land"),
        bridge_advisory_lock(channel) as acquired,
    ):
        assert acquired
        assert session._handle(kind, batch) is True

    assert downloads == [wire]
    landed = Message._base_manager.get(external_id="-10042/44")
    with system_context(reason="test.messaging.telegram.history_ingest.verify"):
        texts = [part.fragment.text for part in landed.parts.all() if part.fragment_id]
    assert "history caption" in texts
    assert any("media unavailable: history.jpg" in text for text in texts)
    assert "_media_facts" in batch[0][0].metadata


@pytest.mark.django_db(transaction=True)
def test_telegram_client_session_path_derives_from_the_passed_store(
    telegram_tables: Any,
    telegram_session_module: ModuleType,
) -> None:
    """The base session-store argument is the sole owner of Telethon's path."""

    session_module = telegram_session_module
    connect = _telegram_module("connect")
    admin = _platform_admin("msg-telegram-store-path-admin")
    with system_context(reason="test.messaging.telegram.store_path.seed"):
        Vendor.objects.create(slug="telegram", display_name="Telegram")
    channel = connect.create_telegram_channel(
        admin,
        name="Store Telegram",
        credential=_app_keys_credential(admin),
    )
    session = session_module.TelegramSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    store = session_store_path(channel) / "custom.session"

    client = session._build_client(store)

    assert client.session == str(store.with_suffix(""))
