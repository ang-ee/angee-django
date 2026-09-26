"""The ``AddonInstaller`` seam — the one writer of ``settings.yaml``'s ``INSTALLED_APPS``.

Installing an addon adds its root to ``settings.yaml`` ``INSTALLED_APPS``;
disabling removes it. ``settings.yaml`` stays the boot source
(no DB-driven settings-load): this module owns the *edit* of that one file, and the
next compose reads it.

The split mirrors an ``ImplClassField`` registry (``VcsBridge.backend_class`` /
``ANGEE_VCS_BACKEND_CLASSES``), in its row-less variant — there is no per-row choice,
it is a per-deployment one, so the selection is a settings key resolved against a
registry rather than a model column:

- :class:`AddonInstaller` owns all YAML logic — the comment-preserving
  ``ruamel.yaml`` round-trip read → edit ``INSTALLED_APPS`` → write.
- :class:`AddonInstallerBackend` is pure transport — it only moves the settings bytes.
  ``local`` (the dev backend, defined here) edits the local ``settings.yaml``. The
  production ``operator`` backend transports that edit over the operator daemon and
  is contributed by the ``platform_integrate_operator`` bridge
  addon, so ``platform`` stays unaware of the operator (they are siblings).

The backend is chosen by ``settings.ANGEE_ADDON_INSTALLER_BACKEND`` against the
``settings.ANGEE_ADDON_INSTALLER_BACKEND_CLASSES`` key→dotted-path registry. This
addon's ``autoconfig`` supplies the default (``local``) and the ``local`` entry;
the ``platform_integrate_operator`` bridge contributes the ``operator`` entry, and a
deployment flips the key to ``operator``. :func:`register_checks` binds a
``manage.py check`` guard over that registry, the row-less analogue of
``ImplClassField.check``.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, MutableMapping, MutableSequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from django.conf import settings
from django.core.checks import CheckMessage, Error, register
from django.core.exceptions import ImproperlyConfigured
from django.core.files import locks
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from angee.base.impl import ImplBase, resolve_all_impl_classes, resolve_impl_class
from angee.fs import write_atomic

_INSTALLED_APPS_KEY = "INSTALLED_APPS"
_SETTINGS_FILENAME = "settings.yaml"
_BACKEND_SETTING = "ANGEE_ADDON_INSTALLER_BACKEND"
_REGISTRY_SETTING = "ANGEE_ADDON_INSTALLER_BACKEND_CLASSES"


class AddonInstallerBackend(ImplBase):
    """Pure transport for the ``settings.yaml`` that lists ``INSTALLED_APPS``.

    The :class:`AddonInstaller` owns all YAML logic; a backend only moves the settings
    bytes. Subclasses register a short :attr:`key` selected by
    ``settings.ANGEE_ADDON_INSTALLER_BACKEND``.
    """

    def read_settings_text(self) -> str:
        """Return the current ``settings.yaml`` text (``FileNotFoundError`` if absent)."""

        raise NotImplementedError

    def write_settings_text(self, text: str) -> None:
        """Write the edited ``settings.yaml`` text back to its source."""

        raise NotImplementedError


class StaleAddonPreviewError(RuntimeError):
    """Raised when settings changed after the reviewed preview."""


class LocalInstallerBackend(AddonInstallerBackend):
    """Edit the local ``settings.yaml`` beside ``manage.py``.

    Reads/writes ``settings.yaml`` under ``settings.BASE_DIR``. The edited addon
    composes when the application is next restarted.
    """

    key = "local"

    def __init__(self) -> None:
        self._expected_text: str | None = None

    def read_settings_text(self) -> str:
        """Return the local ``settings.yaml`` text (``FileNotFoundError`` if absent)."""

        text = self._settings_path().read_text(encoding="utf-8")
        self._expected_text = text
        return text

    def write_settings_text(self, text: str) -> None:
        """Compare and atomically replace the exact settings snapshot last read."""

        expected = self._expected_text
        self._expected_text = None
        if expected is None:
            raise StaleAddonPreviewError("The addon preview is stale; review the changes again.")
        path = self._settings_path()
        lock_path = path.with_name(f".{path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if not locks.lock(handle, locks.LOCK_EX):
                raise OSError(f"Could not lock {path} for an addon settings update.")
            try:
                try:
                    current = path.read_text(encoding="utf-8")
                except FileNotFoundError as error:
                    raise StaleAddonPreviewError(
                        "The addon preview is stale; review the changes again."
                    ) from error
                if current != expected:
                    raise StaleAddonPreviewError(
                        "The addon preview is stale; review the changes again."
                    )
                write_atomic(path, text)
            finally:
                locks.unlock(handle)

    def _settings_path(self) -> Path:
        """Return the project ``settings.yaml`` path, or raise when none is configured."""

        base_dir = getattr(settings, "BASE_DIR", None)
        if not base_dir:
            raise FileNotFoundError("settings.BASE_DIR is not configured; no settings.yaml to edit.")
        return Path(base_dir) / _SETTINGS_FILENAME


@dataclass(frozen=True, slots=True)
class InstallResult:
    """The outcome of one install/disable edit, mapped by a resolver to a report.

    ``already`` is ``True`` when the edit was a no-op — the root was already present
    on install, or already absent on disable. ``refused`` is a non-empty reason when
    the edit was *not applied* — a forced (depended-on) addon, an addon that is not
    materialised, or a deployment whose ``settings.yaml`` cannot be edited — and is
    then the only not-:attr:`ok` outcome.
    """

    name: str
    action: str
    already: bool = False
    refused: str = ""

    @classmethod
    def refusal(cls, name: str, action: str, reason: str) -> InstallResult:
        """Return a not-applied result whose :attr:`summary` is ``reason``."""

        return cls(name=name, action=action, refused=reason)

    @property
    def ok(self) -> bool:
        """Return whether the edit was applied (a refusal is the only not-ok outcome)."""

        return not self.refused

    @property
    def summary(self) -> str:
        """Return the operator-facing one-line summary of this edit (the console message).

        The outcome owns its own human description — the install/disable resolvers
        only relay it — so the wording stays in one place across surfaces. A refusal
        relays its caller-supplied ``reason`` (the policy/availability owner authors it).
        """

        if self.refused:
            return self.refused
        installing = self.action == "install"
        if self.already:
            return f"{self.name} is already installed." if installing else f"{self.name} is not installed."
        if installing:
            return f"Installed {self.name}; the change is pending a restart."
        return f"Disabled {self.name}; the change is pending a restart."


@dataclass(frozen=True, slots=True)
class InstalledAppsSnapshot:
    """One exact settings document and its validated authored roots."""

    text: str
    names: tuple[str, ...]


class AddonInstaller:
    """Owns the comment-preserving ``settings.yaml`` ``INSTALLED_APPS`` edit.

    A backend supplies the bytes; this class does the one thing that
    must preserve operator comments and key order — the ``ruamel.yaml`` round-trip
    edit of the ``INSTALLED_APPS`` sequence in place (append on install, ``remove``
    on disable). Author order is preserved; the composer sorts the dependency
    closure deterministically at boot regardless.
    """

    def __init__(self, backend: AddonInstallerBackend) -> None:
        """Bind the transport backend and a round-trip YAML editor.

        ``ruamel``'s round-trip mode does not auto-detect a file's block-sequence
        indentation and defaults ``best_width`` to ~80, so an unconfigured editor
        reflows every sequence to flush-left and wraps long scalars — defeating the
        comment/layout preservation ruamel exists for here. Pin the project's
        ``  - item`` style (2-space mapping, dash at offset 2 within a 4-space
        sequence indent) and a wide width so an edit stays byte-faithful outside the
        one changed line.
        """

        self.backend = backend
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True
        self._yaml.width = 4096
        self._yaml.indent(mapping=2, sequence=4, offset=2)

    def desired_app_names(self) -> frozenset[str] | None:
        """Return the desired ``INSTALLED_APPS`` roots from ``settings.yaml``.

        An unavailable settings transport returns an unknown value. This prevents a
        provision/migration process without operator configuration from interpreting
        unreadability as an authoritative empty root set.
        """

        if _INSTALLED_APPS_KEY not in getattr(settings, "ANGEE_PROJECT_YAML_SETTINGS", ()):
            return None
        snapshot = self.installed_apps_snapshot()
        return None if snapshot is None else frozenset(snapshot.names)

    def installed_apps_snapshot(self) -> InstalledAppsSnapshot | None:
        """Read and validate one exact editable settings document."""

        if _INSTALLED_APPS_KEY not in getattr(settings, "ANGEE_PROJECT_YAML_SETTINGS", ()):
            return None
        try:
            text = self.backend.read_settings_text()
        except (OSError, NotImplementedError):
            return None
        return InstalledAppsSnapshot(text=text, names=self._parse_app_names(text))

    def installed_app_names(self) -> tuple[str, ...]:
        """Return readable desired roots; retained as the ordered public convenience API."""

        if _INSTALLED_APPS_KEY not in getattr(settings, "ANGEE_PROJECT_YAML_SETTINGS", ()):
            return ()
        snapshot = self.installed_apps_snapshot()
        return snapshot.names if snapshot else ()

    def _parse_app_names(self, text: str) -> tuple[str, ...]:
        """Parse validated roots from already-read settings text."""

        try:
            data = self._yaml.load(text)
        except YAMLError as error:
            raise ImproperlyConfigured(f"Invalid {_SETTINGS_FILENAME}: {error}") from error
        if not isinstance(data, Mapping) or _INSTALLED_APPS_KEY not in data:
            raise ImproperlyConfigured(f"{_SETTINGS_FILENAME} has no {_INSTALLED_APPS_KEY}.")
        return _validated_app_names(data[_INSTALLED_APPS_KEY])

    def install(self, name: str, *, expected_text: str | None = None) -> InstallResult:
        """Add ``name`` to ``INSTALLED_APPS`` idempotently.

        Degrades to a clear refusal when the backend cannot reach an editable
        ``settings.yaml`` (no file, or the configured transport is unavailable) so the
        edge reports it rather than surfacing a raw transport error.
        """

        if _INSTALLED_APPS_KEY not in getattr(settings, "ANGEE_PROJECT_YAML_SETTINGS", ()):
            return InstallResult.refusal(name, "install", _settings_override_refusal())
        try:
            data, apps = self._load_apps(expected_text=expected_text)
        except (OSError, NotImplementedError) as error:
            return InstallResult.refusal(name, "install", _transport_unavailable(error))
        already = name in apps
        if not already:
            apps.append(name)
            self._write(data)
        return InstallResult(name=name, action="install", already=already)

    def disable(self, name: str, *, expected_text: str | None = None) -> InstallResult:
        """Remove ``name`` from ``INSTALLED_APPS`` (no-op when absent).

        Degrades to a clear refusal when the backend cannot reach an editable
        ``settings.yaml`` (see :meth:`install`).
        """

        if _INSTALLED_APPS_KEY not in getattr(settings, "ANGEE_PROJECT_YAML_SETTINGS", ()):
            return InstallResult.refusal(name, "disable", _settings_override_refusal())
        try:
            data, apps = self._load_apps(expected_text=expected_text)
        except (OSError, NotImplementedError) as error:
            return InstallResult.refusal(name, "disable", _transport_unavailable(error))
        already_absent = name not in apps
        if not already_absent:
            apps.remove(name)
            self._write(data)
        return InstallResult(name=name, action="disable", already=already_absent)

    def uninstall(self, name: str, *, expected_text: str | None = None) -> InstallResult:
        """Compatibility alias for :meth:`disable`."""

        return self.disable(name, expected_text=expected_text)

    def apply_app_names(self, names: tuple[str, ...], *, expected_text: str) -> None:
        """Apply reviewed roots to the exact settings document they previewed."""

        data, apps = self._load_apps(expected_text=expected_text)
        if tuple(apps) == names:
            return
        desired = set(names)
        for index in range(len(apps) - 1, -1, -1):
            if apps[index] not in desired:
                del apps[index]
        for index, name in enumerate(names):
            if index < len(apps) and apps[index] == name:
                continue
            if name in apps:
                raise StaleAddonPreviewError("The addon preview is stale; review the changes again.")
            apps.insert(index, name)
        self._write(data)

    def _load_apps(self, *, expected_text: str | None = None) -> tuple[Any, MutableSequence[Any]]:
        """Return the round-trip document and its mutable ``INSTALLED_APPS`` sequence."""

        text = self.backend.read_settings_text()
        if expected_text is not None and text != expected_text:
            raise StaleAddonPreviewError("The addon preview is stale; review the changes again.")
        try:
            data = self._yaml.load(text)
        except YAMLError as error:
            raise ImproperlyConfigured(f"Invalid {_SETTINGS_FILENAME}: {error}") from error
        if not isinstance(data, MutableMapping) or _INSTALLED_APPS_KEY not in data:
            raise ImproperlyConfigured(f"{_SETTINGS_FILENAME} has no {_INSTALLED_APPS_KEY} to edit.")
        apps = data[_INSTALLED_APPS_KEY]
        _validated_app_names(apps)
        return data, apps

    def _write(self, data: Any) -> None:
        """Serialize the edited round-trip document back through the backend."""

        stream = io.StringIO()
        self._yaml.dump(data, stream)
        self.backend.write_settings_text(stream.getvalue())


def _validated_app_names(value: Any) -> tuple[str, ...]:
    """Validate and return the authored addon roots without coercing bad values."""

    if not isinstance(value, MutableSequence):
        raise ImproperlyConfigured(f"{_INSTALLED_APPS_KEY} in {_SETTINGS_FILENAME} must be a list.")
    if any(not isinstance(name, str) or not name for name in value):
        raise ImproperlyConfigured(
            f"{_INSTALLED_APPS_KEY} in {_SETTINGS_FILENAME} must contain non-empty strings."
        )
    return tuple(value)


def _transport_unavailable(error: Exception) -> str:
    """Return a clear refusal message for a backend that cannot edit ``settings.yaml``."""

    if isinstance(error, NotImplementedError):
        return str(error) or "The operator file tools that edit settings.yaml are not available yet."
    # A backend that knows *why* it can't reach settings.yaml (e.g. the operator
    # backend's "operator … unavailable: …") supplies its own reason; the fixed
    # message is only the fallback for a bare FileNotFoundError.
    return str(error) or (
        "This deployment has no editable settings.yaml; "
        "addons are installed by the operator in production."
    )


def _settings_override_refusal() -> str:
    """Explain why editing project YAML cannot change effective roots."""

    return "INSTALLED_APPS is not controlled by the project settings.yaml in this deployment."


def addon_installer() -> AddonInstaller:
    """Return the configured :class:`AddonInstaller`.

    Resolves ``settings.ANGEE_ADDON_INSTALLER_BACKEND`` against the
    ``settings.ANGEE_ADDON_INSTALLER_BACKEND_CLASSES`` registry through the shared
    :func:`~angee.base.impl.resolve_impl_class` owner — the row-less form of
    ``ImplClassField.resolve_class`` (trusted settings path, never row text, with the
    ``AddonInstallerBackend`` subclass check).
    """

    key = getattr(settings, _BACKEND_SETTING, "local")
    backend_cls = resolve_impl_class(_REGISTRY_SETTING, key, AddonInstallerBackend)
    return AddonInstaller(cast(type[AddonInstallerBackend], backend_cls)())


def register_checks() -> None:
    """Register the installer-backend system check (called from ``PlatformConfig.ready``)."""

    register(_check_installer_backends)


def _check_installer_backends(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Validate the installer backend registry and selected key, like ``ImplClassField.check``."""

    del app_configs, kwargs
    errors: list[CheckMessage] = []
    registry = getattr(settings, _REGISTRY_SETTING, {})
    if not isinstance(registry, Mapping) or not registry:
        return [
            Error(
                f"settings.{_REGISTRY_SETTING} must be a non-empty mapping of key to dotted path.",
                id="angee.platform.E001",
            )
        ]
    resolution_errors: list[Exception] = []
    resolve_all_impl_classes(
        _REGISTRY_SETTING,
        AddonInstallerBackend,
        on_error=resolution_errors.append,
    )
    for error in resolution_errors:
        errors.append(
            Error(
                str(error),
                id=(
                    "angee.platform.E002"
                    if isinstance(error, ImportError)
                    else "angee.platform.E003"
                ),
            )
        )
    selected = getattr(settings, _BACKEND_SETTING, "local")
    if selected not in registry:
        errors.append(
            Error(
                f"settings.{_BACKEND_SETTING} = {selected!r} is not a key in settings.{_REGISTRY_SETTING}.",
                id="angee.platform.E004",
            )
        )
    return errors
