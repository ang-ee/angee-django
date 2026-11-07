"""Fresh-interpreter live session hosting, without importing vendor or model code.

The child owns its database locks. Its control pipe also detects parent death,
so an orphan stops without transferring store ownership to a replacement. Keep
this module importable before Django setup: multiprocessing spawn imports the
target module while reconstructing the child.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import time
from multiprocessing.connection import Connection
from typing import Any, NoReturn

from django.core.exceptions import ImproperlyConfigured

from angee.integrate.constants import SESSION_PROCESS_STOP_SECONDS

logger = logging.getLogger(__name__)

PROCESS_REAP_SECONDS = 5.0
"""Final bound for reaping a terminated child."""


class SessionProcessError(RuntimeError):
    """A reaped session child exited without completing its session job."""

    def __init__(self, exitcode: int | None) -> None:
        self.exitcode = exitcode
        super().__init__(f"Live session process exited with code {exitcode}.")


class BridgeSessionProcess:
    """Host one session in a spawned interpreter and return only after reaping it."""

    def __init__(self, model_label: str, pk: Any, *, stop_event: threading.Event) -> None:
        self.model_label = model_label
        self.pk = pk
        self.stop_event = stop_event

    def run(self) -> dict[str, Any]:
        """Spawn without inherited native state, then supervise through a private pipe."""

        if multiprocessing.current_process().daemon:
            raise ImproperlyConfigured(
                "Process-isolated sessions require a non-daemonic worker (threads or solo pool)."
            )
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=run_session_child,
            args=(child, self.model_label, self.pk),
            name=f"bridge-{self.model_label}-{self.pk}",
        )
        result: dict[str, Any] | None = None
        deadline: float | None = None
        try:
            process.start()
            child.close()
            logger.info("Live session child started for %s %s (pid %s).", self.model_label, self.pk, process.pid)
            while process.is_alive():
                if self.stop_event.is_set() and deadline is None:
                    try:
                        parent.send("stop")
                    except BrokenPipeError, EOFError, OSError:
                        pass
                    deadline = time.monotonic() + SESSION_PROCESS_STOP_SECONDS
                if parent.poll(0.2):
                    try:
                        result = parent.recv()
                    except EOFError, OSError:
                        # EOF can precede the OS exit notification. Join rather
                        # than busy-poll the permanently readable closed pipe.
                        process.join(timeout=0.2)
                    if result is not None and deadline is None:
                        deadline = time.monotonic() + PROCESS_REAP_SECONDS
                if deadline is not None and time.monotonic() >= deadline:
                    break
            if process.is_alive():
                process.terminate()
                process.join(timeout=PROCESS_REAP_SECONDS)
            if process.is_alive():
                process.kill()
            process.join(timeout=PROCESS_REAP_SECONDS)
            if process.is_alive():
                raise RuntimeError("The live session child could not be reaped; its store lock remains authoritative.")
            if result is None and parent.poll():
                try:
                    result = parent.recv()
                except EOFError, OSError:
                    pass
            if process.exitcode != 0 or result is None:
                raise SessionProcessError(process.exitcode)
            return result
        finally:
            child.close()
            parent.close()
            if process.pid is not None:
                if process.is_alive():
                    # Closing the pipe requests cooperative stop even when the
                    # parent failed outside its ordinary worker shutdown path.
                    process.join(timeout=SESSION_PROCESS_STOP_SECONDS)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=PROCESS_REAP_SECONDS)
                if not process.is_alive():
                    process.close()


def exit_stalled_session() -> NoReturn:
    """End the interpreter before a stuck vendor can outlive its ownership locks."""

    os._exit(70)


def run_session_child(control: Connection, model_label: str, pk: Any) -> NoReturn:
    """Bootstrap Django after spawn and retain supervision during native calls."""

    stop_event = threading.Event()
    finished = threading.Event()

    def watch_parent() -> None:
        try:
            control.recv()
        except EOFError, OSError:
            pass
        stop_event.set()

    def watch_shutdown() -> None:
        stop_event.wait()
        if not finished.wait(SESSION_PROCESS_STOP_SECONDS):
            exit_stalled_session()

    threading.Thread(target=watch_parent, name="bridge-parent", daemon=True).start()
    threading.Thread(target=watch_shutdown, name="bridge-shutdown", daemon=True).start()
    exitcode = 1
    try:
        # Spawn imports this module before Django's registry exists. Model and
        # session imports belong after setup, exactly as in a Django entrypoint.
        import django

        django.setup()
        from angee.integrate.session_runner import run_bridge_session_job

        result = run_bridge_session_job(
            model_label,
            pk,
            stop_event=stop_event,
            in_child=True,
            on_shutdown=stop_event.set,
            on_stalled_shutdown=exit_stalled_session,
        )
        control.send(result)
        exitcode = 0
    except BaseException:
        logger.exception("Live session child for %s %s failed.", model_label, pk)
    finally:
        finished.set()
        control.close()
        # No native or callback thread may survive completion of a child job.
        os._exit(exitcode)
