"""Live Signal session — the signal-cli JSON-RPC subprocess, worker-only."""

from __future__ import annotations

import json
import logging
import os
import select
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Any

from django.conf import settings

from angee.integrate.live import STOP_JOIN_SECONDS
from angee.messaging.session import LiveChannelSession
from angee.messaging_integrate_signal.identity import SignalMediaFact, parsed_message, receive_envelope

logger = logging.getLogger(__name__)

READ_WAKE_SECONDS = 1.0
"""Maximum wait before the connection thread rechecks cooperative shutdown."""

LIVENESS_INTERVAL_SECONDS = 60.0
"""Quiet interval after which the pipe owner probes ``listAccounts`` inline."""

PID_FILE_NAME = "signal-cli.pid"
"""Store-local orphan identity written after the child starts."""

ORPHAN_REAP_SECONDS = 5.0
"""Maximum wait for a store-matching orphan before SIGKILL escalation."""

_LINK_TIMEOUT_TEXT = "link request timed out"
_LOGGED_OUT_ERROR_TYPES = frozenset(
    {
        "AuthorizationFailedException",
        "InvalidCredentialsException",
        "NotRegisteredException",
        "RegistrationLockException",
        "UnregisteredDeviceException",
    }
)


class SignalCliEof(ConnectionError):
    """signal-cli closed stdout and therefore disconnected this session."""


class SignalCliRpcError(RuntimeError):
    """One JSON-RPC error response with its vendor exception class names."""

    def __init__(self, error: Mapping[str, Any]) -> None:
        self.error = dict(error)
        self.error_types = frozenset(_error_type_names(error))
        super().__init__(str(error.get("message") or "signal-cli JSON-RPC request failed."))


class SignalCliClient:
    """One signal-cli child whose stdin/stdout have exactly one thread owner."""

    def __init__(self, store: Path) -> None:
        self.store = store
        self.pidfile = store / PID_FILE_NAME
        self._next_id = 0
        self._stdout_buffer = bytearray()
        self._reap_orphan()
        binary = str(getattr(settings, "SIGNAL_CLI_BIN", "signal-cli") or "signal-cli")
        self.process: subprocess.Popen[bytes] = subprocess.Popen(
            [binary, "--config", str(store), "jsonRpc"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        if self.process.stdin is None or self.process.stdout is None:
            self.process.kill()
            raise RuntimeError("signal-cli did not expose its JSON-RPC pipes.")
        self.stdin: IO[bytes] = self.process.stdin
        self.stdout: IO[bytes] = self.process.stdout
        self.pidfile.write_text(f"{self.process.pid}\n", encoding="ascii")

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> int:
        """Write one newline-delimited JSON-RPC request and return its id."""

        self._next_id += 1
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_id,
        }
        if params:
            request["params"] = dict(params)
        self.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
        self.stdin.flush()
        return self._next_id

    def read_line(self, timeout: float) -> str | None:
        """Return one stdout line, ``None`` on timeout, or ``""`` on EOF."""

        buffered = self._buffered_line()
        if buffered is not None:
            return buffered
        descriptor = self.stdout.fileno()
        readable, _writable, _exceptional = select.select(
            (descriptor,),
            (),
            (),
            max(0.0, timeout),
        )
        if not readable:
            return None
        chunk = os.read(descriptor, 65_536)
        if not chunk:
            if self._stdout_buffer:
                final = bytes(self._stdout_buffer)
                self._stdout_buffer.clear()
                return final.decode("utf-8", errors="replace")
            return ""
        self._stdout_buffer.extend(chunk)
        return self._buffered_line()

    def _buffered_line(self) -> str | None:
        """Pop one complete UTF-8 line from the raw stdout accumulator."""

        newline = self._stdout_buffer.find(b"\n")
        if newline < 0:
            return None
        raw = bytes(self._stdout_buffer[: newline + 1])
        del self._stdout_buffer[: newline + 1]
        return raw.decode("utf-8", errors="replace")

    def shutdown(self, timeout: float) -> bool:
        """Send SIGTERM, then SIGKILL if needed, within one finite deadline."""

        deadline = time.monotonic() + max(0.0, timeout)
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    try:
                        self.process.wait(timeout=max(0.0, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        return False
            return True
        finally:
            for pipe in (self.stdin, self.stdout):
                try:
                    pipe.close()
                except OSError:
                    pass
            self._remove_own_pidfile()

    def _reap_orphan(self) -> None:
        """Terminate a store-matching child left by a crashed worker."""

        try:
            pid = int(self.pidfile.read_text(encoding="ascii").strip())
        except (FileNotFoundError, OSError, ValueError):  # fmt: skip
            self.pidfile.unlink(missing_ok=True)
            return
        if pid <= 0 or not _process_is_alive(pid):
            self.pidfile.unlink(missing_ok=True)
            return
        command = _process_command(pid)
        if not command or not _command_uses_store(command, self.store):
            logger.error(
                "Refusing to start signal-cli for store %s: live pid %s could not be "
                "verified as the prior store owner (argv=%r).",
                self.store,
                pid,
                command,
            )
            raise RuntimeError(f"Cannot safely verify the live signal-cli process {pid} for {self.store}.")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + ORPHAN_REAP_SECONDS
        while _process_is_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if _process_is_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.pidfile.unlink(missing_ok=True)

    def _remove_own_pidfile(self) -> None:
        """Remove the pidfile only if it still names this client process."""

        try:
            owner = int(self.pidfile.read_text(encoding="ascii").strip())
        except (FileNotFoundError, OSError, ValueError):  # fmt: skip
            return
        if owner == self.process.pid:
            self.pidfile.unlink(missing_ok=True)


class SignalSession(LiveChannelSession):
    """One Signal connection whose JSON-RPC pipe stays on its vendor thread."""

    # The directory itself is signal-cli's credential. An empty child name makes
    # LiveSession's existing-store test and _build_client argument both that dir.
    session_file_name = ""
    client_class: type[SignalCliClient] = SignalCliClient
    _store: Path | None = None

    def _build_client(self, store: Path) -> SignalCliClient:
        """Start signal-cli against this channel's retained config directory."""

        self._store = store
        return self.client_class(store)

    def _connect(self) -> None:
        """Own the JSON-RPC request/read loop on the vendor connection thread."""

        try:
            self._run_client()
        except SignalCliEof:
            pass
        except SignalCliRpcError as error:
            if error.error_types & _LOGGED_OUT_ERROR_TYPES:
                self.events.put(("logged_out", None))
            else:
                self.outcome_error = error
                logger.exception("Signal connection for channel %s failed.", self.bridge.sqid)
        except Exception as error:
            self.outcome_error = error
            logger.exception("Signal connection for channel %s crashed.", self.bridge.sqid)
        finally:
            self.events.put(("disconnected", None))

    def _run_client(self) -> None:
        """Resume or pair one account, then stream receive notifications."""

        accounts = self._accounts(self._rpc("listAccounts"))
        if accounts:
            account = accounts[0]
        else:
            account = self._pair()
        if not account or self._vendor_stopping():
            return
        self.events.put(("paired", account))
        self._stream(account)

    def _pair(self) -> str:
        """Rotate startLink QR tokens until finishLink provisions an account."""

        while not self._vendor_stopping():
            result = self._rpc("startLink")
            uri = _device_link_uri(result)
            if not uri:
                raise RuntimeError("signal-cli startLink returned no deviceLinkUri.")
            self.events.put(("qr", uri.encode("utf-8")))
            try:
                finish_result = self._rpc(
                    "finishLink",
                    {"deviceLinkUri": uri, "deviceName": "Angee"},
                )
            except SignalCliRpcError as error:
                if _LINK_TIMEOUT_TEXT in str(error).lower():
                    continue
                raise
            accounts = self._accounts(self._rpc("listAccounts"))
            account = accounts[0] if accounts else _account_from_result(finish_result)
            if not account:
                raise RuntimeError("signal-cli linked a device but returned no account.")
            self._rpc("sendSyncRequest", {"account": account})
            return account
        return ""

    def _stream(self, account: str) -> None:
        """Read receive notifications and issue an inline quiet liveness probe."""

        next_probe = time.monotonic() + LIVENESS_INTERVAL_SECONDS
        while not self._vendor_stopping():
            timeout = min(READ_WAKE_SECONDS, max(0.0, next_probe - time.monotonic()))
            record = self._read_record(timeout)
            if record is not None:
                self._handle_notification(record)
                next_probe = time.monotonic() + LIVENESS_INTERVAL_SECONDS
                continue
            if time.monotonic() < next_probe:
                continue
            accounts = self._accounts(self._rpc("listAccounts"))
            if self._vendor_stopping():
                return
            if account not in accounts:
                self.events.put(("logged_out", None))
                return
            next_probe = time.monotonic() + LIVENESS_INTERVAL_SECONDS

    def _rpc(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        """Issue one RPC and match its response while translating notifications."""

        request_id = self.client.request(method, params)
        while not self._vendor_stopping():
            record = self._read_record(READ_WAKE_SECONDS)
            if record is None:
                continue
            if record.get("method") == "receive":
                self._handle_notification(record)
                continue
            if record.get("id") != request_id:
                logger.debug(
                    "Ignoring unmatched signal-cli response id %r for channel %s.",
                    record.get("id"),
                    self.bridge.sqid,
                )
                continue
            error = record.get("error")
            if isinstance(error, Mapping):
                raise SignalCliRpcError(error)
            return record.get("result")
        return None

    def _read_record(self, timeout: float) -> Mapping[str, Any] | None:
        """Decode one JSON object, distinguishing a quiet pipe from EOF."""

        line = self.client.read_line(timeout)
        if line is None:
            return None
        if line == "":
            raise SignalCliEof("signal-cli closed its JSON-RPC stdout.")
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.info("Ignoring malformed signal-cli JSON for channel %s.", self.bridge.sqid)
            return None
        if not isinstance(record, Mapping):
            logger.info("Ignoring non-object signal-cli JSON for channel %s.", self.bridge.sqid)
            return None
        return record

    def _handle_notification(self, record: Mapping[str, Any]) -> None:
        """Translate one receive envelope and enqueue only neutral messages."""

        if record.get("method") != "receive":
            return
        envelope = receive_envelope(record)
        if envelope is None:
            return
        if exception := envelope.get("exception"):
            logger.info(
                "Skipping Signal receive exception for channel %s: %s",
                self.bridge.sqid,
                exception,
            )
            return
        parsed = parsed_message(envelope)
        if parsed is not None:
            self.events.put(("messages", [(parsed, envelope)]))

    def _download(self, _payload: Any, fact: SignalMediaFact) -> bytes | None:
        """Read one signal-cli-owned attachment; cleanup waits for ingest commit."""

        path = self._attachment_path(fact)
        if path is None:
            return None
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            logger.info(
                "Signal attachment %s for channel %s could not be read.",
                fact.id,
                self.bridge.sqid,
            )
            return None

    def _after_ingest(self, batch: list[tuple[Any, Any]], landed: list[Any]) -> None:
        """Remove attachments only for messages the ingest owner landed."""

        landed_ids = {str(message.external_id) for message in landed}
        for message, _payload in batch:
            if str(message.external_id) not in landed_ids:
                continue
            for fact in message.metadata.get("_media_facts", ()):
                path = self._attachment_path(fact)
                if path is None:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.info(
                        "Signal attachment %s for channel %s could not be removed after ingest.",
                        fact.id,
                        self.bridge.sqid,
                    )

    def _attachment_path(self, fact: SignalMediaFact) -> Path | None:
        """Return one validated store-local Signal attachment path."""

        store = self._store
        attachment_id = str(getattr(fact, "id", "") or "").strip()
        if store is None or not attachment_id or Path(attachment_id).name != attachment_id:
            return None
        return store / "attachments" / attachment_id

    def _shutdown(self, connection: threading.Thread) -> bool:
        """Stop signal-cli, then join its connection thread within one bound."""

        deadline = time.monotonic() + STOP_JOIN_SECONDS
        stopped = True
        if self.client is not None:
            stopped = self.client.shutdown(max(0.0, deadline - time.monotonic()))
        connection.join(timeout=max(0.0, deadline - time.monotonic()))
        return stopped and not connection.is_alive()

    @staticmethod
    def _accounts(result: object) -> tuple[str, ...]:
        """Normalize listAccounts' string or object rows to E.164 ids."""

        if not isinstance(result, Sequence) or isinstance(result, str | bytes | bytearray):
            return ()
        accounts: list[str] = []
        for row in result:
            account = _account_from_result(row)
            if account:
                accounts.append(account)
        return tuple(accounts)


def _device_link_uri(result: object) -> str:
    if isinstance(result, Mapping):
        return str(result.get("deviceLinkUri") or "").strip()
    return str(result or "").strip() if isinstance(result, str) else ""


def _account_from_result(result: object) -> str:
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, Mapping):
        for key in ("number", "account", "username"):
            if value := str(result.get(key) or "").strip():
                return value
    return ""


def _error_type_names(value: object) -> set[str]:
    """Return the vendor exception class named by structured ``error.data.type``."""

    if not isinstance(value, Mapping) or not isinstance(value.get("data"), Mapping):
        return set()
    error_type = value["data"].get("type")
    if not isinstance(error_type, str) or not error_type.strip():
        return set()
    return {error_type.strip().rsplit(".", 1)[-1].rsplit("$", 1)[-1]}


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_command(pid: int) -> tuple[str, ...]:
    """Return one live process's argv from procfs or the native ps fallback."""

    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = proc_cmdline.read_bytes()
    except OSError:
        raw = b""
    if raw:
        return tuple(part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part)
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ()
    try:
        return tuple(shlex.split(result.stdout.strip()))
    except ValueError:
        return ()


def _command_uses_store(command: Sequence[str], store: Path) -> bool:
    """Return whether argv carries this exact store after ``--config``."""

    try:
        index = command.index("--config")
        configured = Path(command[index + 1])
    except (ValueError, IndexError):  # fmt: skip
        return False
    candidates = [configured]
    try:
        mode_index = command.index("jsonRpc", index + 2)
    except ValueError:
        mode_index = index + 2
    if mode_index > index + 2:
        candidates.append(Path(" ".join(command[index + 1 : mode_index])))
    resolved_store = store.resolve()
    return any(candidate.resolve() == resolved_store for candidate in candidates)
