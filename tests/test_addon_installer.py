"""Tests for the AddonInstaller seam — the one writer of settings.yaml's INSTALLED_APPS.

The ``local`` backend edits a real ``settings.yaml`` (comment-preserving via
``ruamel.yaml``); ``addon_installer()`` resolves the configured backend the way an
``ImplClassField`` resolves an impl key. (The production ``operator`` backend lives in
the ``platform_integrate_operator`` bridge addon; it is tested there.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.platform.installer import (
    AddonInstaller,
    LocalInstallerBackend,
    addon_installer,
)

_SETTINGS_YAML = """\
# Project composition facts — operator comments must survive an install edit.
SECRET_KEY: dev-key

INSTALLED_APPS:
  - angee.platform  # the console host
  - example.notes
ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"
"""


def _local_installer(tmp_path: Path, settings: Any) -> AddonInstaller:
    """Return an AddonInstaller over a temp ``settings.yaml`` the local backend edits."""

    (tmp_path / "settings.yaml").write_text(_SETTINGS_YAML, encoding="utf-8")
    settings.BASE_DIR = tmp_path
    return AddonInstaller(LocalInstallerBackend())


def test_local_install_appends_and_preserves_comments(tmp_path: Path, settings: Any) -> None:
    """Install appends the root and preserves operator comments + key order/layout."""

    installer = _local_installer(tmp_path, settings)

    result = installer.install("example.demo")

    assert result.already is False
    assert result.rebuild_status == "pending"  # local backend recomposes on next boot
    assert installer.installed_app_names() == ("angee.platform", "example.notes", "example.demo")

    text = (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    assert "# Project composition facts" in text  # top comment survived
    assert "# the console host" in text  # inline comment survived
    assert 'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"' in text  # other keys untouched
    assert text.index("angee.platform") < text.index("example.notes") < text.index("example.demo")  # author order


def test_local_install_preserves_sequence_indentation(tmp_path: Path, settings: Any) -> None:
    """The ruamel editor keeps the project's ``  - item`` indentation, not just comments.

    An unconfigured round-trip editor reflows every block sequence to flush-left and
    wraps long scalars; the installer pins the project's 2-space style so the unchanged
    region stays byte-faithful and only the appended line is new.
    """

    installer = _local_installer(tmp_path, settings)

    installer.install("example.demo")

    lines = (tmp_path / "settings.yaml").read_text(encoding="utf-8").splitlines()
    # Existing entries keep their exact 2-space-dash indentation (+ inline comment).
    assert "  - angee.platform  # the console host" in lines
    assert "  - example.notes" in lines
    # The appended root carries the same indentation, never flush-left.
    assert "  - example.demo" in lines
    assert "- example.demo" not in lines  # i.e. not de-indented to column 0
    # Untouched mapping keys are byte-identical.
    assert "SECRET_KEY: dev-key" in lines
    assert 'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"' in lines


def test_local_install_is_idempotent(tmp_path: Path, settings: Any) -> None:
    """Installing an already-present root is a no-op (``already`` True), file unchanged."""

    installer = _local_installer(tmp_path, settings)
    before = (tmp_path / "settings.yaml").read_text(encoding="utf-8")

    result = installer.install("angee.platform")

    assert result.already is True
    assert (tmp_path / "settings.yaml").read_text(encoding="utf-8") == before


def test_local_uninstall_removes_then_absent_is_noop(tmp_path: Path, settings: Any) -> None:
    """Uninstall removes the root; uninstalling an absent root is a no-op."""

    installer = _local_installer(tmp_path, settings)

    removed = installer.uninstall("example.notes")
    assert removed.already is False
    assert installer.installed_app_names() == ("angee.platform",)
    text = (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    assert "example.notes" not in text
    assert "# the console host" in text  # surviving entry keeps its comment

    absent = installer.uninstall("never.installed")
    assert absent.already is True


def test_install_then_uninstall_round_trips_comments(tmp_path: Path, settings: Any) -> None:
    """An install followed by an uninstall leaves the comments and other keys intact."""

    installer = _local_installer(tmp_path, settings)

    installer.install("example.demo")
    installer.uninstall("example.demo")

    text = (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    assert "example.demo" not in text
    assert "# Project composition facts" in text
    assert "# the console host" in text
    assert installer.installed_app_names() == ("angee.platform", "example.notes")


def test_installed_app_names_is_empty_when_unreadable(tmp_path: Path, settings: Any) -> None:
    """A missing settings.yaml degrades to an empty desired set, never an error."""

    settings.BASE_DIR = tmp_path  # no settings.yaml written

    assert AddonInstaller(LocalInstallerBackend()).installed_app_names() == ()


def test_addon_installer_resolves_local_default(settings: Any) -> None:
    """``addon_installer()`` resolves the default ``local`` backend from the registry."""

    settings.ANGEE_ADDON_INSTALLER_BACKEND = "local"

    assert isinstance(addon_installer().backend, LocalInstallerBackend)


def test_addon_installer_rejects_unknown_key(settings: Any) -> None:
    """An unknown selected key fails fast against the registry."""

    settings.ANGEE_ADDON_INSTALLER_BACKEND = "nope"

    with pytest.raises(ImproperlyConfigured, match="ANGEE_ADDON_INSTALLER_BACKEND"):
        addon_installer()


def test_addon_installer_rejects_non_backend_class(settings: Any) -> None:
    """A registry entry that is not an AddonInstallerBackend subclass is rejected."""

    settings.ANGEE_ADDON_INSTALLER_BACKEND = "bad"
    settings.ANGEE_ADDON_INSTALLER_BACKEND_CLASSES = {"bad": "angee.platform.models.Addon"}

    with pytest.raises(ImproperlyConfigured, match="AddonInstallerBackend"):
        addon_installer()
