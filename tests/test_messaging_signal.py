"""Tests for the Signal channel addon without a real signal-cli binary."""

from __future__ import annotations

import importlib
import io
import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.live import PairingState, session_store_path
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.sync import BridgeProgressReporter
from tests.conftest import (
    SchemaAddon,
    Vendor,
    _clear_model_tables,
    _create_missing_tables,
    execute_schema,
    result_data,
)
from tests.test_messaging import MESSAGING_TEST_MODELS, Message, Part, _storage_drive
from tests.test_messaging_graphql import (
    Channel,
    _platform_admin,
    _request,
    iam_schema,
    integrate_schema,
    messaging_schema,
    parties_schema,
)

SIGNAL_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel)

_PEER_UUID = "11111111-1111-4111-8111-111111111111"
_OTHER_UUID = "22222222-2222-4222-8222-222222222222"
_OWN_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_GROUP_ID = "U2lnbmFsR3JvdXBJZA=="


def _signal_module(name: str) -> ModuleType:
    """Import one Signal addon module, failing as an unmet feature."""

    try:
        return importlib.import_module(f"angee.messaging_integrate_signal.{name}")
    except ModuleNotFoundError:
        pytest.fail(f"The Signal {name} module is not implemented.")


@pytest.fixture
def signal_tables(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Create concrete messaging tables and isolate Signal session storage."""

    settings.ANGEE_DATA_DIR = str(tmp_path / "data")
    settings.SIGNAL_CLI_BIN = "/opt/angee/bin/signal-cli"
    monkeypatch.setattr("angee.integrate.impl.enqueue_task", lambda *args, **kwargs: None)
    created_models = _create_missing_tables(SIGNAL_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(SIGNAL_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _inbound(
    *,
    source_uuid: str = _PEER_UUID,
    source_number: str = "+420777123456",
    source_name: str = "Ada",
    timestamp: int = 1_768_800_000_001,
    text: str = "Hello from Signal",
    group: bool = False,
    quote: Mapping[str, Any] | None = None,
    attachments: Sequence[Mapping[str, Any]] = (),
    **data: Any,
) -> dict[str, Any]:
    """Return one JSON-schema-shaped inbound Signal envelope fixture."""

    message: dict[str, Any] = {
        "timestamp": timestamp,
        "message": text,
        "attachments": list(attachments),
        **data,
    }
    if group:
        message["groupInfo"] = {"groupId": _GROUP_ID, "groupName": "Signal Group"}
    if quote is not None:
        message["quote"] = dict(quote)
    return {
        "source": source_number,
        "sourceNumber": source_number,
        "sourceUuid": source_uuid,
        "sourceName": source_name,
        "sourceDevice": 1,
        "timestamp": timestamp,
        "dataMessage": message,
    }


def _outbound(*, timestamp: int = 1_768_800_000_004) -> dict[str, Any]:
    """Return one primary-device sent-message echo fixture."""

    return {
        "source": "+420700000000",
        "sourceNumber": "+420700000000",
        "sourceUuid": _OWN_UUID,
        "sourceName": "Me",
        "sourceDevice": 1,
        "timestamp": timestamp,
        "syncMessage": {
            "sentMessage": {
                "destination": "+420777123456",
                "destinationNumber": "+420777123456",
                "destinationUuid": _PEER_UUID,
                "timestamp": timestamp,
                "message": "Sent from my phone",
                "attachments": [],
            }
        },
    }


def _receive(envelope: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "receive", "params": {"envelope": dict(envelope)}}


def _response(request_id: int, result: Any = None, *, error: Mapping[str, Any] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    record["error" if error is not None else "result"] = dict(error) if error is not None else result
    return record


def test_signal_identity_maps_direct_uuid_handle_and_message_id() -> None:
    """Direct chats key on the peer UUID while labels remain mutable."""

    identity = _signal_module("identity")
    parsed = identity.parsed_message(_inbound())

    assert parsed is not None
    assert parsed.external_id == f"{_PEER_UUID}/{_PEER_UUID}:1768800000001"
    assert parsed.platform == "signal"
    assert parsed.direction == "inbound"
    assert parsed.sender == identity.ParsedHandle(
        platform="signal",
        external_id=_PEER_UUID,
        value="+420777123456",
        display_name="Ada",
    )
    assert parsed.thread == identity.ParsedThread(external_id=_PEER_UUID, modality="direct", title="")
    assert parsed.body is not None and parsed.body.text == "Hello from Signal"
    assert parsed.sent_at is not None and int(parsed.sent_at.timestamp() * 1000) == 1_768_800_000_001


def test_signal_identity_maps_group_quote_and_each_attachment_fact() -> None:
    """Groups, quoted sender/timestamps, and store attachment ids stay lossless."""

    identity = _signal_module("identity")
    parsed = identity.parsed_message(
        _inbound(
            source_uuid=_OTHER_UUID,
            timestamp=1_768_800_000_003,
            text="Reply with two files",
            group=True,
            quote={"authorUuid": _PEER_UUID, "id": 1_768_800_000_002},
            attachments=(
                {"id": "att-one", "contentType": "image/jpeg", "filename": "one.jpg", "size": 3},
                {"id": "att-two", "contentType": "application/pdf", "filename": "two.pdf", "size": 3},
            ),
        )
    )

    assert parsed is not None and parsed.thread is not None
    assert parsed.thread.external_id == _GROUP_ID
    assert parsed.thread.modality == "group"
    assert parsed.thread.title == "Signal Group"
    assert parsed.external_id == f"{_GROUP_ID}/{_OTHER_UUID}:1768800000003"
    assert parsed.in_reply_to == f"{_GROUP_ID}/{_PEER_UUID}:1768800000002"
    assert parsed.metadata["_media_facts"] == (
        identity.SignalMediaFact("att-one", "image/jpeg", "one.jpg"),
        identity.SignalMediaFact("att-two", "application/pdf", "two.pdf"),
    )


def test_signal_identity_maps_sent_sync_as_outbound_echo() -> None:
    """The user's phone-sent sync echo uses its destination UUID as chat key."""

    identity = _signal_module("identity")
    parsed = identity.parsed_message(_outbound())

    assert parsed is not None
    assert parsed.direction == "outbound"
    assert parsed.external_id == f"{_PEER_UUID}/{_OWN_UUID}:1768800000004"
    assert parsed.thread is not None and parsed.thread.external_id == _PEER_UUID
    assert parsed.sender is not None and parsed.sender.external_id == _OWN_UUID


@pytest.mark.parametrize(
    "envelope",
    [
        {**_inbound(), "receiptMessage": {"timestamps": [1_768_800_000_001]}},
        {**_inbound(), "typingMessage": {"action": "STARTED"}},
        {**_inbound(), "storyMessage": {}},
        _inbound(reaction={"emoji": "👍"}),
        _inbound(remoteDelete={"timestamp": 1_768_800_000_001}),
        _inbound(payment={"note": "ignored"}),
        _inbound(pollCreate={"question": "ignored"}),
    ],
)
def test_signal_identity_skips_v1_non_message_shapes(envelope: Mapping[str, Any]) -> None:
    """Reactions, deletes, receipts, typing, stories, payments, and polls skip."""

    assert _signal_module("identity").parsed_message(envelope) is None


@pytest.mark.django_db(transaction=True)
def test_create_signal_channel_uses_seeded_vendor_without_credential(signal_tables: None) -> None:
    """The config directory is the credential; no Credential row is attached."""

    connect = _signal_module("connect")
    admin = _platform_admin("msg-signal-connect-admin")
    with system_context(reason="test.messaging.signal.vendor.seed"):
        Vendor.objects.create(slug="signal", display_name="Signal")

    channel = connect.create_signal_channel(admin)

    with system_context(reason="test.messaging.signal.connect.verify"):
        channel.refresh_from_db()
        assert channel.owner_id == admin.pk
        assert channel.created_by_id == admin.pk
        assert channel.vendor.slug == "signal"
        assert channel.backend_class == "signal"
        assert channel.display_name == "Signal"
        assert channel.credential_id is None
        assert channel.lifecycle == "connected"
        assert channel.subscription_state["desired"] == Channel.LiveState.LIVE


@pytest.mark.django_db(transaction=True)
def test_connect_signal_channel_mutation_dispatches_to_service(signal_tables: None) -> None:
    """The no-input vendor mutation returns the shared Channel projection."""

    admin = _platform_admin("msg-signal-graphql-admin")
    with system_context(reason="test.messaging.signal.graphql.seed"):
        Vendor.objects.create(slug="signal", display_name="Signal")
    signal_schema = _signal_module("schema")
    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema, parties_schema, messaging_schema, signal_schema)
    ]
    schema = GraphQLSchemas(addons).build("console")

    result = execute_schema(
        schema,
        """
        mutation ConnectSignal {
          connect_signal_channel {
            id
            display_name
            backend_class
            lifecycle
          }
        }
        """,
        request=_request(admin),
    )

    payload = result_data(result)["connect_signal_channel"]
    assert payload == {
        "id": payload["id"],
        "display_name": "Signal",
        "backend_class": "SIGNAL",
        "lifecycle": "CONNECTED",
    }


class _ScriptedStdin(io.BytesIO):
    """Writable JSON-RPC stdin that keeps its transcript after close."""

    def close(self) -> None:
        self.flush()


class _ScriptedPopen:
    """Popen fake backed by a selectable OS pipe with scripted JSON lines."""

    scripts: list[list[Mapping[str, Any] | str]] = []
    instances: list[_ScriptedPopen] = []
    stubborn = False
    write_delay = 0.01

    def __init__(self, command: list[str], **kwargs: Any) -> None:
        assert kwargs["stdin"] is subprocess.PIPE
        assert kwargs["stdout"] is subprocess.PIPE
        assert "text" not in kwargs
        assert "encoding" not in kwargs
        assert kwargs["bufsize"] == 0
        assert kwargs["start_new_session"] is True
        self.command = command
        self.pid = 32_000 + len(type(self).instances)
        self.stdin = _ScriptedStdin()
        read_fd, write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self._write_fd = write_fd
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        script = type(self).scripts.pop(0)
        self.writer = threading.Thread(target=self._write_script, args=(script,), daemon=True)
        self.writer.start()
        type(self).instances.append(self)

    def _write_script(self, script: list[Mapping[str, Any] | str]) -> None:
        try:
            for record in script:
                line = record if isinstance(record, str) else json.dumps(record)
                os.write(self._write_fd, (line + "\n").encode("utf-8"))
                time.sleep(type(self).write_delay)
            time.sleep(0.3)
        except OSError:
            pass
        finally:
            try:
                os.close(self._write_fd)
            except OSError:
                pass

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if not type(self).stubborn:
            self.returncode = -signal.SIGTERM

    def kill(self) -> None:
        self.killed = True
        self.returncode = -signal.SIGKILL

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.command, timeout=0)
        return self.returncode


@pytest.fixture
def scripted_signal_cli(monkeypatch: pytest.MonkeyPatch) -> type[_ScriptedPopen]:
    """Replace subprocess.Popen while retaining real selectable stdout pipes."""

    _ScriptedPopen.scripts = []
    _ScriptedPopen.instances = []
    _ScriptedPopen.stubborn = False
    _ScriptedPopen.write_delay = 0.01
    monkeypatch.setattr(subprocess, "Popen", _ScriptedPopen)
    return _ScriptedPopen


def _rpc_transcript(process: _ScriptedPopen) -> list[dict[str, Any]]:
    return [json.loads(line) for line in process.stdin.getvalue().decode("utf-8").splitlines()]


@pytest.mark.django_db(transaction=True)
def test_signal_full_session_pairs_and_ingests_receive_stream(
    signal_tables: None,
    scripted_signal_cli: type[_ScriptedPopen],
    tmp_path: Path,
) -> None:
    """Pair, sync, ingest direct/group/reply/media/echo, skip trust failure, EOF."""

    connect = _signal_module("connect")
    session_module = _signal_module("session")
    admin = _platform_admin("msg-signal-session-admin")
    with system_context(reason="test.messaging.signal.session.seed"):
        Vendor.objects.create(slug="signal", display_name="Signal")
        _storage_drive(tmp_path / "storage", owner=admin)
    channel = connect.create_signal_channel(admin)
    store = session_store_path(channel)
    attachments = store / "attachments"
    attachments.mkdir(parents=True)
    (attachments / "att-one").write_bytes(b"one")
    (attachments / "att-two").write_bytes(b"two")

    group_parent = _inbound(timestamp=1_768_800_000_002, text="Group parent", group=True)
    group_reply = _inbound(
        source_uuid=_OTHER_UUID,
        source_number="+420777654321",
        source_name="Grace",
        timestamp=1_768_800_000_003,
        text="Reply with two files",
        group=True,
        quote={"authorUuid": _PEER_UUID, "id": 1_768_800_000_002},
        attachments=(
            {"id": "att-one", "contentType": "image/jpeg", "filename": "one.jpg", "size": 3},
            {"id": "att-two", "contentType": "application/pdf", "filename": "two.pdf", "size": 3},
        ),
    )
    untrusted = {**_inbound(timestamp=1_768_800_000_005), "exception": "UntrustedIdentityException"}
    scripted_signal_cli.scripts.append(
        [
            _response(1, []),
            _response(2, {"deviceLinkUri": "sgnl://linkdevice?uuid=test&pub_key=test"}),
            _response(3, {"deviceLinkUri": "sgnl://linkdevice?uuid=test&pub_key=test"}),
            _response(4, ["+420700000000"]),
            _response(5, {}),
            _receive(_inbound()),
            _receive(group_parent),
            _receive(group_reply),
            _receive(_outbound()),
            _receive(untrusted),
        ]
    )
    reports: list[dict[str, Any]] = []
    real_reporter = BridgeProgressReporter(channel)

    class _Reporter:
        def report(self, stage: Any, **payload: Any) -> None:
            reports.append({"stage": stage, **payload})
            real_reporter.report(stage, **payload)

    session = session_module.SignalSession(channel, reporter=_Reporter(), stop_event=threading.Event())
    with system_context(reason="test.messaging.signal.session.run"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        outcome = session.run()

    assert outcome is PairingState.PAIRED
    process = scripted_signal_cli.instances[-1]
    assert process.command == [
        "/opt/angee/bin/signal-cli",
        "--config",
        str(store),
        "jsonRpc",
    ]
    assert [request["method"] for request in _rpc_transcript(process)] == [
        "listAccounts",
        "startLink",
        "finishLink",
        "listAccounts",
        "sendSyncRequest",
    ]
    assert _rpc_transcript(process)[2]["params"] == {
        "deviceLinkUri": "sgnl://linkdevice?uuid=test&pub_key=test",
        "deviceName": "Angee",
    }
    assert _rpc_transcript(process)[4]["params"] == {"account": "+420700000000"}
    assert process.terminated is True
    assert process.killed is False
    assert not (store / "signal-cli.pid").exists()
    assert not (attachments / "att-one").exists()
    assert not (attachments / "att-two").exists()

    with system_context(reason="test.messaging.signal.session.verify"):
        channel.refresh_from_db()
        assert channel.subscription_state["own_id"] == "+420700000000"
        assert channel.backend.pairing().account_label == "+420700000000"
        landed = list(Message._base_manager.filter(channel=channel).order_by("sent_at"))
        assert len(landed) == 4
        assert [message.direction for message in landed] == [
            "inbound",
            "inbound",
            "inbound",
            "outbound",
        ]
        reply = Message._base_manager.get(external_id=f"{_GROUP_ID}/{_OTHER_UUID}:1768800000003")
        assert reply.thread.external_id.endswith(f":{_GROUP_ID}")
        assert reply.metadata["signal_timestamp"] == "1768800000003"
        assert "_media_facts" not in reply.metadata
        file_parts = list(Part._base_manager.filter(message=reply, file__isnull=False).select_related("file"))
        assert [part.name for part in file_parts] == ["one.jpg", "two.pdf"]
        assert [part.file.open_stream().read() for part in file_parts] == [b"one", b"two"]
    pairing_states = [report.get("details", {}).get("pairing", {}).get("state") for report in reports]
    assert PairingState.AWAITING_SCAN in pairing_states
    assert PairingState.PAIRED in pairing_states


@pytest.mark.parametrize("error_type", ["NotRegisteredException", "AuthorizationFailedException"])
@pytest.mark.django_db(transaction=True)
def test_signal_auth_errors_report_logged_out_without_deleting_store(
    signal_tables: None,
    scripted_signal_cli: type[_ScriptedPopen],
    error_type: str,
) -> None:
    """Registration/auth class names release the session but never wipe its store."""

    connect = _signal_module("connect")
    session_module = _signal_module("session")
    admin = _platform_admin(f"msg-signal-logged-out-{error_type}")
    with system_context(reason="test.messaging.signal.logged_out.seed"):
        Vendor.objects.create(slug="signal", display_name="Signal")
    channel = connect.create_signal_channel(admin)
    store = session_store_path(channel)
    store.mkdir(parents=True)
    marker = store / "retained-account.db"
    marker.write_bytes(b"retained")
    scripted_signal_cli.scripts.append(
        [
            _response(
                1,
                error={
                    "code": -1,
                    "message": f"Account unavailable ({error_type})",
                    "data": {"type": f"org.asamk.signal.exceptions.{error_type}"},
                },
            )
        ]
    )
    session = session_module.SignalSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.client = session._build_client(store)

    session._connect()

    assert session.events.get_nowait() == ("logged_out", None)
    assert session.events.get_nowait() == ("disconnected", None)
    assert marker.read_bytes() == b"retained"
    session.client.shutdown(0.1)


@pytest.mark.django_db(transaction=True)
def test_signal_stdout_eof_reports_disconnected(
    signal_tables: None,
    scripted_signal_cli: type[_ScriptedPopen],
) -> None:
    """An exited signal-cli child surfaces the generic disconnected event."""

    connect = _signal_module("connect")
    session_module = _signal_module("session")
    admin = _platform_admin("msg-signal-eof-admin")
    with system_context(reason="test.messaging.signal.eof.seed"):
        Vendor.objects.create(slug="signal", display_name="Signal")
    channel = connect.create_signal_channel(admin)
    store = session_store_path(channel)
    store.mkdir(parents=True)
    scripted_signal_cli.scripts.append([_response(1, ["+420700000000"])])
    session = session_module.SignalSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.client = session._build_client(store)

    session._connect()

    assert session.events.get_nowait() == ("paired", "+420700000000")
    assert session.events.get_nowait() == ("disconnected", None)
    session.client.shutdown(0.1)


def test_signal_link_timeout_rotates_qr(scripted_signal_cli: type[_ScriptedPopen], tmp_path: Path) -> None:
    """A timed-out finishLink response starts a fresh URI and publishes a new QR."""

    session_module = _signal_module("session")
    scripted_signal_cli.scripts.append(
        [
            _response(1, {"deviceLinkUri": "sgnl://linkdevice?uuid=first"}),
            _response(2, error={"code": -1, "message": "Link request timed out", "data": None}),
            _response(3, {"deviceLinkUri": "sgnl://linkdevice?uuid=second"}),
            _response(4, {}),
            _response(5, ["+420700000000"]),
            _response(6, {}),
        ]
    )
    bridge = _BareBridge(tmp_path)
    session = session_module.SignalSession(
        bridge,
        reporter=_NoopReporter(),
        stop_event=threading.Event(),
    )
    session.client = session._build_client(tmp_path)

    account = session._pair()

    assert session.events.get_nowait() == ("qr", b"sgnl://linkdevice?uuid=first")
    assert session.events.get_nowait() == ("qr", b"sgnl://linkdevice?uuid=second")
    assert account == "+420700000000"
    session.client.shutdown(0.1)


def test_signal_shutdown_escalates_from_sigterm_to_sigkill(
    scripted_signal_cli: type[_ScriptedPopen],
    tmp_path: Path,
) -> None:
    """A child ignoring SIGTERM is killed before the shared stop deadline."""

    session_module = _signal_module("session")
    scripted_signal_cli.stubborn = True
    scripted_signal_cli.scripts.append([])
    client = session_module.SignalCliClient(tmp_path)

    assert client.shutdown(0.01) is True
    process = scripted_signal_cli.instances[-1]
    assert process.terminated is True
    assert process.killed is True


def test_signal_raw_pipe_reader_drains_a_burst_without_waiting_for_fd_readiness(
    scripted_signal_cli: type[_ScriptedPopen],
    tmp_path: Path,
) -> None:
    """Complete JSON lines already in the accumulator never strand behind select."""

    session_module = _signal_module("session")
    scripted_signal_cli.write_delay = 0.0
    scripted_signal_cli.scripts.append([_response(index, {"index": index}) for index in range(1, 51)])
    client = session_module.SignalCliClient(tmp_path)

    records = [json.loads(client.read_line(1.0) or "null") for _ in range(50)]

    assert [record["id"] for record in records] == list(range(1, 51))
    client.shutdown(0.1)


def test_signal_quiet_liveness_probe_detects_missing_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connection thread owns the quiet listAccounts probe and logout event."""

    session_module = _signal_module("session")
    monkeypatch.setattr(session_module, "LIVENESS_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(session_module, "READ_WAKE_SECONDS", 0.0)

    class _LivenessClient:
        def __init__(self) -> None:
            self.requests: list[tuple[str, Mapping[str, Any] | None]] = []

        def request(self, method: str, params: Mapping[str, Any] | None = None) -> int:
            self.requests.append((method, params))
            return len(self.requests)

        def read_line(self, _timeout: float) -> str | None:
            if not self.requests:
                return None
            return json.dumps(_response(len(self.requests), [])) + "\n"

    session = session_module.SignalSession(
        _BareBridge(tmp_path),
        reporter=_NoopReporter(),
        stop_event=threading.Event(),
    )
    client = _LivenessClient()
    session.client = client

    session._stream("+420700000000")

    assert client.requests == [("listAccounts", None)]
    assert session.events.get_nowait() == ("logged_out", None)


def test_signal_quiet_liveness_probe_stop_does_not_report_logged_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop arriving during listAccounts exits without releasing the account claim."""

    session_module = _signal_module("session")
    monkeypatch.setattr(session_module, "LIVENESS_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(session_module, "READ_WAKE_SECONDS", 0.0)
    stop_event = threading.Event()

    class _QuietClient:
        def read_line(self, _timeout: float) -> None:
            return None

    session = session_module.SignalSession(
        _BareBridge(tmp_path),
        reporter=_NoopReporter(),
        stop_event=stop_event,
    )
    session.client = _QuietClient()

    def stop_during_probe(_method: str, _params: Mapping[str, Any] | None = None) -> None:
        stop_event.set()
        return None

    monkeypatch.setattr(session, "_rpc", stop_during_probe)

    session._stream("+420700000000")

    assert session.events.empty()


def test_signal_orphan_reaping_requires_matching_store_cmdline(
    scripted_signal_cli: type[_ScriptedPopen],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pidfile can signal only a live signal-cli process on this config dir."""

    session_module = _signal_module("session")
    (tmp_path / "signal-cli.pid").write_text("1234\n", encoding="ascii")
    scripted_signal_cli.scripts.append([])
    signals: list[tuple[int, int]] = []
    alive = iter((True, False, False))
    monkeypatch.setattr(session_module, "_process_is_alive", lambda _pid: next(alive))
    monkeypatch.setattr(
        session_module,
        "_process_command",
        lambda _pid: ("signal-cli", "--config", str(tmp_path), "jsonRpc"),
    )
    monkeypatch.setattr(session_module.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    client = session_module.SignalCliClient(tmp_path)

    assert signals == [(1234, signal.SIGTERM)]
    client.shutdown(0.1)


def test_signal_store_match_recovers_unquoted_ps_path_with_spaces(tmp_path: Path) -> None:
    """The ps fallback compares the full resolved --config argument before jsonRpc."""

    session_module = _signal_module("session")
    store = tmp_path / "signal account"

    assert session_module._command_uses_store(
        ("signal-cli", "--config", str(tmp_path / "signal"), "account", "jsonRpc"),
        store,
    )


def test_signal_unverified_live_orphan_blocks_a_second_child(
    scripted_signal_cli: type[_ScriptedPopen],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable live argv fails safe instead of risking concurrent store access."""

    session_module = _signal_module("session")
    (tmp_path / "signal-cli.pid").write_text("1234\n", encoding="ascii")
    monkeypatch.setattr(session_module, "_process_is_alive", lambda _pid: True)
    monkeypatch.setattr(session_module, "_process_command", lambda _pid: ())

    with pytest.raises(RuntimeError, match="Cannot safely verify"):
        session_module.SignalCliClient(tmp_path)

    assert scripted_signal_cli.instances == []


def test_signal_attachment_cleanup_waits_for_landed_external_id(tmp_path: Path) -> None:
    """A failed ingest leaves signal-cli media available for the retry."""

    identity = _signal_module("identity")
    session_module = _signal_module("session")
    envelope = _inbound(attachments=({"id": "att-one", "contentType": "image/jpeg"},))
    message = identity.parsed_message(envelope)
    assert message is not None
    fact = message.metadata["_media_facts"][0]
    attachment = tmp_path / "attachments" / "att-one"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"content")
    session = session_module.SignalSession(
        _BareBridge(tmp_path),
        reporter=_NoopReporter(),
        stop_event=threading.Event(),
    )
    session._store = tmp_path

    assert session._download(envelope, fact) == b"content"
    assert attachment.exists()

    session._after_ingest([(message, envelope)], [])
    assert attachment.exists()

    session._after_ingest(
        [(message, envelope)],
        [type("Landed", (), {"external_id": message.external_id})()],
    )
    assert not attachment.exists()


def test_signal_auth_error_type_ignores_exception_names_in_human_message() -> None:
    """Only structured error.data.type can classify a session as logged out."""

    session_module = _signal_module("session")
    error = session_module.SignalCliRpcError(
        {
            "message": "A peer mentioned NotRegisteredException in profile text",
            "data": {"type": "org.asamk.signal.exceptions.NetworkFailureException"},
        }
    )

    assert error.error_types == {"NetworkFailureException"}


class _NoopReporter:
    def report(self, _stage: Any, **_payload: Any) -> None:
        return None


class _BareLiveImpl:
    key = "signal"
    label = "Signal"
    state_identity_key = "own_id"


class _BareBridge:
    """Minimal non-DB bridge for direct connection-thread protocol tests."""

    sqid = "bare"
    subscription_state: dict[str, Any] = {}
    live_impl = _BareLiveImpl()

    def __init__(self, _store: Path) -> None:
        self.subscription_state = {}
