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
_WEB_PACKAGE_JSON_PATH = "web/package.json"


class OperatorInstallerBackend(AddonInstallerBackend):
    """Production transport: edit ``app/settings.yaml`` + rebuild through the operator.

    Each read seeds that file's etag, and the matching write echoes it for the
    daemon's optimistic-concurrency check (the read/write pair runs within one
    ``AddonInstaller.install``/``uninstall`` call on this one instance). An
    unconfigured or unreachable daemon surfaces as ``FileNotFoundError`` on the read,
    which the installer turns into a clean refusal rather than a crash.
    """

    key = "operator"

    def __init__(self) -> None:
        """Resolve the operator daemon from settings; the read seeds the write etag."""

        self._daemon = OperatorDaemon.from_settings()
        self._settings_etag = ""
        self._web_package_json_etag = ""

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
        self._settings_etag = remote.etag
        return remote.content

    def write_settings_text(self, text: str) -> None:
        """Write the edited ``app/settings.yaml`` back through the operator with the read etag."""

        self._settings_etag = self._daemon.write_file(_SOURCE, _SETTINGS_PATH, text, self._settings_etag)

    def read_web_package_json_text(self) -> str:
        """Return ``app/web/package.json`` via the operator, seeding its concurrency etag."""

        try:
            remote = self._daemon.read_file(_SOURCE, _WEB_PACKAGE_JSON_PATH)
        except (OSError, RuntimeError) as error:
            raise FileNotFoundError(
                f"operator {_SOURCE}/{_WEB_PACKAGE_JSON_PATH} unavailable: {error}"
            ) from error
        self._web_package_json_etag = remote.etag
        return remote.content

    def write_web_package_json_text(self, text: str) -> None:
        """Write the edited ``app/web/package.json`` through the operator with the read etag."""

        self._web_package_json_etag = self._daemon.write_file(
            _SOURCE,
            _WEB_PACKAGE_JSON_PATH,
            text,
            self._web_package_json_etag,
        )

    def request_rebuild(self) -> str:
        """Trigger the operator rebuild + restart (``POST /stack/build``)."""

        return self._daemon.stack_build()
