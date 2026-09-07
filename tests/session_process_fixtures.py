"""Import-safe targets for real spawned live-session supervision tests."""

from __future__ import annotations

import os
import signal
import sys
import time
from multiprocessing.connection import Connection
from pathlib import Path
from types import ModuleType
from typing import Any


def fake_session_child(control: Connection, mode: str, options: dict[str, Any]) -> None:
    """Run a small process with observable completion, crash, and stop behavior."""

    ready = Path(options["ready"])
    if mode == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    ready.write_text(str(os.getpid()))
    if mode == "crash":
        os._exit(86)
    if mode == "empty":
        return
    if mode == "delayed_exit":
        control.send({"ok": True, "pid": os.getpid()})
        time.sleep(0.3)
        Path(options["completed"]).write_text("native cleanup completed")
        return
    if mode == "stubborn":
        while True:
            time.sleep(0.05)
    ticks = 0
    while not control.poll(0.02):
        ticks += 1
        next_report = ready.with_name(f"{ready.name}.next")
        next_report.write_text(f"{os.getpid()}:{ticks}")
        next_report.replace(ready)
    assert control.recv() == "stop"
    control.send({"ok": True, "pid": os.getpid(), "ticks": ticks})


def isolated_session_child(control: Connection, mode: str, options: dict[str, Any]) -> None:
    """Exercise the production child bootstrap with an isolated fake session job."""

    import django

    from angee.integrate import session_process

    # Only the database/session job is replaced. Production pipe supervision,
    # stop event, watchdog, result delivery, and interpreter exit all run here.
    django.setup = lambda: None
    session_process.SESSION_PROCESS_STOP_SECONDS = 0.2
    runner = ModuleType("angee.integrate.session_runner")

    def run_job(_model: str, _pk: Any, **kwargs: Any) -> dict[str, Any]:
        Path(options["ready"]).write_text(str(os.getpid()))
        if mode == "teardown-stuck":
            kwargs["on_shutdown"]()
        if mode in ("stuck", "teardown-stuck"):
            while True:
                time.sleep(0.05)
        if mode == "failure":
            raise RuntimeError("A vendor failed inside its isolated process.")
        if mode == "cooperative":
            assert kwargs["stop_event"].wait(3.0), "The parent stop never reached the session job."
        return {
            "ok": True,
            "pid": os.getpid(),
            "in_child": kwargs["in_child"],
            "has_shutdown": kwargs["on_shutdown"] == kwargs["stop_event"].set,
            "has_stalled_shutdown": kwargs["on_stalled_shutdown"] is session_process.exit_stalled_session,
            "vendor_loaded": any(name.startswith("neonize") for name in sys.modules),
        }

    runner.run_bridge_session_job = run_job
    sys.modules[runner.__name__] = runner
    session_process.run_session_child(control, mode, options)
