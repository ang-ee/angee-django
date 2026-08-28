"""The ``operator`` AddonInstaller backend — settings.yaml editing over the operator.

The :class:`~angee.platform.installer.AddonInstaller` owns the comment-preserving
``INSTALLED_APPS`` edit; a backend is pure transport. This one routes that transport
through the operator daemon: the operator owns the deployment's ``settings.yaml`` and
the rebuild lifecycle, so the read/write go over its file API and the rebuild over
``POST /stack/build`` (which rebuilds and restarts the django service). The read's
``etag`` is carried to the write so a concurrent edit fails the write rather than
silently clobbering it.
"""

from __future__ import annotations

from angee.operator.daemon import OperatorDaemon
from angee.platform.installer import AddonInstallerBackend

_SOURCE = "app"
_SETTINGS_PATH = "settings.yaml"


class OperatorInstallerBackend(AddonInstallerBackend):
    """Production transport: edit ``app/settings.yaml`` + rebuild through the operator.

    ``read_settings_text`` seeds :attr:`_etag` from the operator read; the matching
    ``write_settings_text`` echoes it for the daemon's optimistic-concurrency check
    (the two run within one ``AddonInstaller.install``/``uninstall`` call on this one
    instance). An unconfigured or unreachable daemon surfaces as ``FileNotFoundError``
    on the read, which the installer turns into a clean refusal rather than a crash.
    """

    key = "operator"

    def __init__(self) -> None:
        """Resolve the operator daemon from settings; the read seeds the write etag."""

        self._daemon = OperatorDaemon.from_settings()
        self._etag = ""

    def read_settings_text(self) -> str:
        """Return ``app/settings.yaml`` via the operator, seeding the concurrency etag.

        Any transport-layer failure (daemon unconfigured, unreachable, or an HTTP
        error) becomes ``FileNotFoundError`` so the installer reports a refusal — the
        read is the gate before any edit, so a broken transport never half-applies.
        """

        try:
            remote = self._daemon.read_file(_SOURCE, _SETTINGS_PATH)
        except (OSError, RuntimeError) as error:
            raise FileNotFoundError(f"operator {_SOURCE}/{_SETTINGS_PATH} unavailable: {error}") from error
        self._etag = remote.etag
        return remote.content

    def write_settings_text(self, text: str) -> None:
        """Write the edited ``app/settings.yaml`` back through the operator with the read etag."""

        self._etag = self._daemon.write_file(_SOURCE, _SETTINGS_PATH, text, self._etag)

    def request_rebuild(self) -> str:
        """Trigger the operator rebuild + restart (``POST /stack/build``)."""

        return self._daemon.stack_build()
