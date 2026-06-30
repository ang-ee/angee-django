"""The ``AddonInstaller`` seam — the one writer of ``settings.yaml``'s ``INSTALLED_APPS``.

Installing an addon = adding its root to ``settings.yaml`` ``INSTALLED_APPS`` and
rebuilding; uninstalling = removing it. ``settings.yaml`` stays the boot source
(no DB-driven settings-load): this module owns the *edit* of that one file, and the
next compose reads it.

The split mirrors an ``ImplClassField`` registry (``VcsBridge.backend_class`` /
``ANGEE_VCS_BACKEND_CLASSES``), in its row-less variant — there is no per-row choice,
it is a per-deployment one, so the selection is a settings key resolved against a
registry rather than a model column:

- :class:`AddonInstaller` owns all YAML logic — the comment-preserving
  ``ruamel.yaml`` round-trip read → edit ``INSTALLED_APPS`` → write → request rebuild.
- :class:`AddonInstallerBackend` is pure transport — it only moves the settings bytes
  and asks for a rebuild. ``local`` (the dev stub, defined here) edits the local
  ``settings.yaml`` and treats rebuild as a pending no-op (the addon composes on the
  next ``angee dev`` boot). The production ``operator`` backend — edit + rebuild over
  the operator daemon — is contributed by the ``platform_integrate_operator`` bridge
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
from typing import Any, ClassVar, cast

from django.conf import settings
from django.core.checks import CheckMessage, Error, register
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from ruamel.yaml import YAML

from angee.base.registry import resolve_impl_class
from angee.fs import write_atomic

_INSTALLED_APPS_KEY = "INSTALLED_APPS"
_SETTINGS_FILENAME = "settings.yaml"
_BACKEND_SETTING = "ANGEE_ADDON_INSTALLER_BACKEND"
_REGISTRY_SETTING = "ANGEE_ADDON_INSTALLER_BACKEND_CLASSES"


class AddonInstallerBackend:
    """Pure transport for the ``settings.yaml`` that lists ``INSTALLED_APPS`` + the rebuild.

    The :class:`AddonInstaller` owns all YAML logic; a backend only moves the settings
    bytes and asks for a rebuild. Subclasses register a short :attr:`key` selected by
    ``settings.ANGEE_ADDON_INSTALLER_BACKEND``.
    """

    key: ClassVar[str] = ""

    def read_settings_text(self) -> str:
        """Return the current ``settings.yaml`` text (``FileNotFoundError`` if absent)."""

        raise NotImplementedError

    def write_settings_text(self, text: str) -> None:
        """Write the edited ``settings.yaml`` text back to its source."""

        raise NotImplementedError

    def request_rebuild(self) -> str:
        """Trigger a rebuild/restart and return a short status marker."""

        raise NotImplementedError


class LocalInstallerBackend(AddonInstallerBackend):
    """Dev stub: edit the local ``settings.yaml`` beside ``manage.py``; rebuild is pending.

    Reads/writes ``settings.yaml`` under ``settings.BASE_DIR`` and treats the rebuild
    as a no-op pending marker — the edited addon composes on the next ``angee dev``
    boot. Makes the whole install/uninstall flow work and testable in dev.
    """

    key = "local"

    def read_settings_text(self) -> str:
        """Return the local ``settings.yaml`` text (``FileNotFoundError`` if absent)."""

        return self._settings_path().read_text(encoding="utf-8")

    def write_settings_text(self, text: str) -> None:
        """Write the edited text atomically, the way the composer writes generated files."""

        write_atomic(self._settings_path(), text)

    def request_rebuild(self) -> str:
        """Mark the rebuild pending — the addon composes on the next ``angee dev`` boot."""

        return "pending"

    def _settings_path(self) -> Path:
        """Return the project ``settings.yaml`` path, or raise when none is configured."""

        base_dir = getattr(settings, "BASE_DIR", None)
        if not base_dir:
            raise FileNotFoundError("settings.BASE_DIR is not configured; no settings.yaml to edit.")
        return Path(base_dir) / _SETTINGS_FILENAME


@dataclass(frozen=True, slots=True)
class InstallResult:
    """The outcome of one install/uninstall edit, mapped by a resolver to a report.

    ``already`` is ``True`` when the edit was a no-op — the root was already present
    on install, or already absent on uninstall. ``refused`` is a non-empty reason when
    the edit was *not applied* — a forced (depended-on) addon, an addon that is not
    materialised, or a deployment whose ``settings.yaml`` cannot be edited — and is
    then the only not-:attr:`ok` outcome.
    """

    name: str
    action: str
    already: bool = False
    rebuild_status: str = ""
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

        The outcome owns its own human description — the install/uninstall resolvers
        only relay it — so the wording stays in one place across surfaces. A refusal
        relays its caller-supplied ``reason`` (the policy/availability owner authors it).
        """

        if self.refused:
            return self.refused
        installing = self.action == "install"
        if self.already:
            return f"{self.name} is already installed." if installing else f"{self.name} is not installed."
        if installing:
            return f"Installed {self.name}; it composes on the next rebuild ({self.rebuild_status})."
        return f"Uninstalled {self.name}; it is removed on the next rebuild ({self.rebuild_status})."


class AddonInstaller:
    """Owns the comment-preserving ``settings.yaml`` ``INSTALLED_APPS`` edit.

    A backend supplies the bytes and the rebuild; this class does the one thing that
    must preserve operator comments and key order — the ``ruamel.yaml`` round-trip
    edit of the ``INSTALLED_APPS`` sequence in place (append on install, ``remove``
    on uninstall). Author order is preserved; the composer sorts the dependency
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

    def installed_app_names(self) -> tuple[str, ...]:
        """Return the desired ``INSTALLED_APPS`` roots from ``settings.yaml``.

        Best-effort: returns ``()`` when the file is absent/unreadable (bare test
        settings, or the ``operator`` backend not active), so the reconcile that reads
        this for the ``pending`` diff never raises.
        """

        try:
            text = self.backend.read_settings_text()
        except (FileNotFoundError, NotImplementedError):
            return ()
        data = self._yaml.load(text) or {}
        apps = data.get(_INSTALLED_APPS_KEY) if isinstance(data, Mapping) else None
        if not isinstance(apps, MutableSequence):
            return ()
        return tuple(str(name) for name in apps)

    def install(self, name: str) -> InstallResult:
        """Add ``name`` to ``INSTALLED_APPS`` (idempotent), then request a rebuild.

        Degrades to a clear refusal when the backend cannot reach an editable
        ``settings.yaml`` (no file, or the operator transport is not built yet) so the
        edge reports it rather than surfacing a raw transport error.
        """

        try:
            data, apps = self._load_apps()
        except (FileNotFoundError, NotImplementedError) as error:
            return InstallResult.refusal(name, "install", _transport_unavailable(error))
        already = name in apps
        if not already:
            apps.append(name)
            self._write(data)
        return InstallResult(
            name=name, action="install", already=already, rebuild_status=self.backend.request_rebuild()
        )

    def uninstall(self, name: str) -> InstallResult:
        """Remove ``name`` from ``INSTALLED_APPS`` (no-op when absent), then request a rebuild.

        Degrades to a clear refusal when the backend cannot reach an editable
        ``settings.yaml`` (see :meth:`install`).
        """

        try:
            data, apps = self._load_apps()
        except (FileNotFoundError, NotImplementedError) as error:
            return InstallResult.refusal(name, "uninstall", _transport_unavailable(error))
        already_absent = name not in apps
        if not already_absent:
            apps.remove(name)
            self._write(data)
        return InstallResult(
            name=name, action="uninstall", already=already_absent, rebuild_status=self.backend.request_rebuild()
        )

    def _load_apps(self) -> tuple[Any, MutableSequence[Any]]:
        """Return the round-trip document and its mutable ``INSTALLED_APPS`` sequence."""

        data = self._yaml.load(self.backend.read_settings_text())
        if not isinstance(data, MutableMapping) or _INSTALLED_APPS_KEY not in data:
            raise ImproperlyConfigured(f"{_SETTINGS_FILENAME} has no {_INSTALLED_APPS_KEY} to edit.")
        apps = data[_INSTALLED_APPS_KEY]
        if not isinstance(apps, MutableSequence):
            raise ImproperlyConfigured(f"{_INSTALLED_APPS_KEY} in {_SETTINGS_FILENAME} must be a list.")
        return data, apps

    def _write(self, data: Any) -> None:
        """Serialize the edited round-trip document back through the backend."""

        stream = io.StringIO()
        self._yaml.dump(data, stream)
        self.backend.write_settings_text(stream.getvalue())


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


def addon_installer() -> AddonInstaller:
    """Return the configured :class:`AddonInstaller`.

    Resolves ``settings.ANGEE_ADDON_INSTALLER_BACKEND`` against the
    ``settings.ANGEE_ADDON_INSTALLER_BACKEND_CLASSES`` registry through the shared
    :func:`~angee.base.registry.resolve_impl_class` owner — the row-less form of
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
    for key, dotted in registry.items():
        try:
            backend_cls = import_string(str(dotted))
        except ImportError as error:
            errors.append(
                Error(
                    f"settings.{_REGISTRY_SETTING}[{key!r}] = {dotted!r} does not import: {error}",
                    id="angee.platform.E002",
                )
            )
            continue
        if not (isinstance(backend_cls, type) and issubclass(backend_cls, AddonInstallerBackend)):
            errors.append(
                Error(
                    f"settings.{_REGISTRY_SETTING}[{key!r}] = {dotted!r} is not an AddonInstallerBackend subclass.",
                    id="angee.platform.E003",
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
