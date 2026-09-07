"""Fault-injection coverage for fresh-process live bridge hosting."""

from __future__ import annotations

import multiprocessing
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.integrate import session_process
from angee.integrate.session_process import BridgeSessionProcess, SessionProcessError
from tests.session_process_fixtures import fake_session_child, isolated_session_child


def wait_for_file(path: Path) -> str:
    """Wait a bounded time for a spawned child to announce its state."""

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if path.exists() and (text := path.read_text()):
            return text
        time.sleep(0.01)
    pytest.fail(f"Spawned child did not report {path.name} within five seconds.")


def assert_reaped(pid: int) -> None:
    """The host must return with no remaining process or zombie for this pid."""

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_spawned_result_waits_for_native_cleanup_and_process_reaping(monkeypatch, tmp_path):
    monkeypatch.setattr(session_process, "run_session_child", fake_session_child)
    completed = tmp_path / "completed"
    result = BridgeSessionProcess(
        "delayed_exit",
        {"ready": str(tmp_path / "ready"), "completed": str(completed)},
        stop_event=threading.Event(),
    ).run()

    assert result["ok"] is True
    assert result["pid"] != os.getpid()
    assert completed.read_text() == "native cleanup completed"
    assert_reaped(result["pid"])


@pytest.mark.parametrize(("mode", "exitcode"), [("crash", 86), ("empty", 0)])
def test_native_exit_without_result_is_reaped_and_reported(monkeypatch, tmp_path, mode, exitcode):
    monkeypatch.setattr(session_process, "run_session_child", fake_session_child)
    ready = tmp_path / "ready"
    with pytest.raises(SessionProcessError) as error:
        BridgeSessionProcess(mode, {"ready": str(ready)}, stop_event=threading.Event()).run()

    assert error.value.exitcode == exitcode
    assert_reaped(int(ready.read_text()))


def test_native_crash_leaves_another_session_running(monkeypatch, tmp_path):
    monkeypatch.setattr(session_process, "run_session_child", fake_session_child)
    survivor_ready = tmp_path / "survivor"
    crash_ready = tmp_path / "crash"
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        survivor = executor.submit(BridgeSessionProcess("healthy", {"ready": str(survivor_ready)}, stop_event=stop).run)
        try:
            first_report = wait_for_file(survivor_ready)
            with pytest.raises(SessionProcessError) as error:
                BridgeSessionProcess("crash", {"ready": str(crash_ready)}, stop_event=threading.Event()).run()
            assert error.value.exitcode == 86
            assert not survivor.done()
            survivor_pid = int(first_report.split(":")[0])
            assert survivor_pid != int(crash_ready.read_text())
            os.kill(survivor_pid, 0)
            after_crash_report = survivor_ready.read_text()
            deadline = time.monotonic() + 2.0
            while survivor_ready.read_text() == after_crash_report and time.monotonic() < deadline:
                time.sleep(0.01)
            assert survivor_ready.read_text() != after_crash_report
        finally:
            stop.set()
        result = survivor.result(timeout=5.0)

    assert result["ticks"] > 0
    assert_reaped(result["pid"])
    assert_reaped(int(crash_ready.read_text()))


def test_stubborn_child_is_killed_and_reaped_within_shutdown_bound(monkeypatch, tmp_path):
    monkeypatch.setattr(session_process, "run_session_child", fake_session_child)
    monkeypatch.setattr(session_process, "SESSION_PROCESS_STOP_SECONDS", 0.1)
    monkeypatch.setattr(session_process, "PROCESS_REAP_SECONDS", 0.1)
    ready = tmp_path / "ready"
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        child = executor.submit(BridgeSessionProcess("stubborn", {"ready": str(ready)}, stop_event=stop).run)
        try:
            pid = int(wait_for_file(ready))
            stop.set()
            with pytest.raises(SessionProcessError) as error:
                child.result(timeout=5.0)
        finally:
            stop.set()

    assert error.value.exitcode == -signal.SIGKILL
    assert_reaped(pid)


def test_daemonic_worker_rejects_process_hosting_before_spawning(monkeypatch):
    monkeypatch.setattr(session_process.multiprocessing, "current_process", lambda: SimpleNamespace(daemon=True))
    with pytest.raises(ImproperlyConfigured, match="non-daemonic worker"):
        BridgeSessionProcess("unused", 1, stop_event=threading.Event()).run()


def test_production_child_receives_stop_and_returns_job_outcome(monkeypatch, tmp_path):
    monkeypatch.setattr(session_process, "run_session_child", isolated_session_child)
    ready = tmp_path / "ready"
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        child = executor.submit(BridgeSessionProcess("cooperative", {"ready": str(ready)}, stop_event=stop).run)
        try:
            wait_for_file(ready)
        finally:
            stop.set()
        result = child.result(timeout=5.0)

    assert result["in_child"] is True
    assert result["has_shutdown"] is True
    assert result["has_stalled_shutdown"] is True
    assert result["vendor_loaded"] is False
    assert_reaped(result["pid"])


@pytest.mark.parametrize("parent_action", ["stop", "eof"])
def test_production_child_watchdog_exits_stuck_job_on_stop_or_parent_eof(tmp_path, parent_action):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    ready = tmp_path / "ready"
    process = context.Process(target=isolated_session_child, args=(child, "stuck", {"ready": str(ready)}))
    process.start()
    child.close()
    try:
        pid = int(wait_for_file(ready))
        if parent_action == "stop":
            parent.send("stop")
        else:
            parent.close()
        process.join(timeout=5.0)
        assert process.exitcode == 70
        assert_reaped(pid)
    finally:
        parent.close()
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)
        process.close()


def test_production_child_job_exception_has_no_success_result(monkeypatch, tmp_path):
    monkeypatch.setattr(session_process, "run_session_child", isolated_session_child)
    ready = tmp_path / "ready"
    with pytest.raises(SessionProcessError) as error:
        BridgeSessionProcess("failure", {"ready": str(ready)}, stop_event=threading.Event()).run()

    assert error.value.exitcode == 1
    assert_reaped(int(ready.read_text()))


def test_production_child_watchdog_bounds_stuck_teardown_without_parent_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(session_process, "run_session_child", isolated_session_child)
    monkeypatch.setattr(session_process, "SESSION_PROCESS_STOP_SECONDS", 0.5)
    monkeypatch.setattr(session_process, "PROCESS_REAP_SECONDS", 0.2)
    ready = tmp_path / "ready"
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        child = executor.submit(BridgeSessionProcess("teardown-stuck", {"ready": str(ready)}, stop_event=stop).run)
        try:
            with pytest.raises(SessionProcessError) as error:
                child.result(timeout=5.0)
            assert not stop.is_set()
        finally:
            stop.set()

    assert error.value.exitcode == 70
    assert_reaped(int(ready.read_text()))
