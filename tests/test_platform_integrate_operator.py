"""Tests for the operator AddonInstaller backend (the platform_integrate_operator bridge).

The bridge contributes the ``operator`` backend into platform's installer registry. It
routes the AddonInstaller's read/write/rebuild transport through the operator daemon
(the file API + ``/stack/build``), carrying the read ``etag`` to the write so a
concurrent edit fails rather than clobbering.
"""

from __future__ import annotations

import pytest

from angee.operator.daemon import OperatorDaemonError, RemoteFile
from angee.platform_integrate_operator.autoconfig import SETTINGS
from angee.platform_integrate_operator.installer import OperatorInstallerBackend


class _FakeDaemon:
    """A stand-in operator daemon recording its file-tool calls."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str, str]] = []
        self.last_read: tuple[str, str] | None = None

    def read_file(self, source: str, path: str) -> RemoteFile:
        self.last_read = (source, path)
        return RemoteFile(content="INSTALLED_APPS:\n  - angee.iam\n", etag="etag-1")

    def write_file(self, source: str, path: str, content: str, etag: str) -> str:
        self.writes.append((source, path, content, etag))
        return "etag-2"

    def stack_build(self) -> str:
        return "rebuilding"


def _backend_with(daemon: object) -> OperatorInstallerBackend:
    """Build the backend (which resolves a daemon from settings) and swap in ``daemon``."""

    backend = OperatorInstallerBackend()
    backend._daemon = daemon  # type: ignore[assignment]
    return backend


def test_operator_backend_round_trips_settings_through_the_daemon() -> None:
    """read seeds the etag; write echoes it back for the app source; rebuild calls stack_build."""

    daemon = _FakeDaemon()
    backend = _backend_with(daemon)

    assert backend.read_settings_text() == "INSTALLED_APPS:\n  - angee.iam\n"
    assert backend._etag == "etag-1"  # the read seeds the concurrency etag
    assert daemon.last_read == ("app", "settings.yaml")

    backend.write_settings_text("INSTALLED_APPS:\n  - angee.iam\n  - x\n")
    # the write carries the read's etag (optimistic concurrency) for the app source
    assert daemon.writes == [("app", "settings.yaml", "INSTALLED_APPS:\n  - angee.iam\n  - x\n", "etag-1")]
    assert backend._etag == "etag-2"  # the write returns the new etag

    assert backend.request_rebuild() == "rebuilding"


def test_operator_backend_read_failure_becomes_a_refusal() -> None:
    """A daemon transport failure on read surfaces as FileNotFoundError.

    The read is the gate before any edit; the AddonInstaller catches FileNotFoundError
    there and turns it into a clean refusal rather than half-applying or crashing.
    """

    class _FailDaemon:
        def read_file(self, source: str, path: str) -> RemoteFile:
            raise OperatorDaemonError("operator GET files: connection refused")

    backend = _backend_with(_FailDaemon())
    with pytest.raises(FileNotFoundError, match="unavailable"):
        backend.read_settings_text()


def test_autoconfig_contributes_the_operator_backend() -> None:
    """The bridge's autoconfig adds the ``operator`` entry into platform's registry (dotted-key merge)."""

    assert (
        SETTINGS["ANGEE_ADDON_INSTALLER_BACKEND_CLASSES.operator"]
        == "angee.platform_integrate_operator.installer.OperatorInstallerBackend"
    )
