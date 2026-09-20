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
    StaleAddonPreviewError,
    _check_installer_backends,
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
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})
    return AddonInstaller(LocalInstallerBackend())


def test_local_install_appends_and_preserves_comments(tmp_path: Path, settings: Any) -> None:
    """Install appends the root and preserves operator comments + key order/layout."""

    installer = _local_installer(tmp_path, settings)

    result = installer.install("example.demo")

    assert result.already is False
    assert result.summary == "Installed example.demo; the change is pending a restart."
    assert installer.installed_app_names() == ("angee.platform", "example.notes", "example.demo")

    text = (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    assert "# Project composition facts" in text  # top comment survived
    assert "# the console host" in text  # inline comment survived
    assert 'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"' in text  # other keys untouched
    assert text.index("angee.platform") < text.index("example.notes") < text.index("example.demo")  # author order


def test_snapshot_then_apply_preserves_comments(tmp_path: Path, settings: Any) -> None:
    """A preview parse cannot consume comment state needed by the confirmed edit."""

    installer = _local_installer(tmp_path, settings)
    snapshot = installer.installed_apps_snapshot()

    assert snapshot is not None
    installer.apply_app_names((*snapshot.names, "example.demo"), expected_text=snapshot.text)

    text = (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    assert "# Project composition facts" in text
    assert "  - angee.platform  # the console host" in text
    assert "  - example.demo" in text


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


def test_reviewed_roots_reject_a_changed_settings_document(tmp_path: Path, settings: Any) -> None:
    """The bytes actually edited must be the bytes used by the preview."""

    installer = _local_installer(tmp_path, settings)
    snapshot = installer.installed_apps_snapshot()
    assert snapshot is not None
    (tmp_path / "settings.yaml").write_text(
        _SETTINGS_YAML.replace("example.notes", "example.changed"), encoding="utf-8"
    )

    with pytest.raises(StaleAddonPreviewError, match="preview is stale"):
        installer.apply_app_names(
            (*snapshot.names, "example.demo"), expected_text=snapshot.text
        )


def test_local_backend_rejects_a_concurrent_writer_after_read(
    tmp_path: Path, settings: Any
) -> None:
    """Two writers cannot both replace the same settings snapshot."""

    _local_installer(tmp_path, settings)
    first = LocalInstallerBackend()
    second = LocalInstallerBackend()
    original = first.read_settings_text()
    assert second.read_settings_text() == original

    first.write_settings_text(original.replace("example.notes", "example.first"))

    with pytest.raises(StaleAddonPreviewError, match="preview is stale"):
        second.write_settings_text(original.replace("example.notes", "example.second"))

    assert "example.first" in (tmp_path / "settings.yaml").read_text(encoding="utf-8")


def test_local_backend_refuses_a_write_without_a_read_snapshot(
    tmp_path: Path, settings: Any
) -> None:
    """A direct transport write fails closed without compare-and-swap input."""

    _local_installer(tmp_path, settings)

    with pytest.raises(StaleAddonPreviewError, match="preview is stale"):
        LocalInstallerBackend().write_settings_text(_SETTINGS_YAML)


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


def test_desired_app_names_is_unknown_when_unreadable(tmp_path: Path, settings: Any) -> None:
    """A missing settings.yaml remains distinct from an authoritative empty list."""

    settings.BASE_DIR = tmp_path  # no settings.yaml written
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})

    installer = AddonInstaller(LocalInstallerBackend())
    assert installer.desired_app_names() is None
    assert installer.installed_app_names() == ()


def test_desired_app_names_rejects_non_string_members(tmp_path: Path, settings: Any) -> None:
    """Invalid roots fail clearly instead of being silently stringified."""

    (tmp_path / "settings.yaml").write_text("INSTALLED_APPS:\n  - 42\n", encoding="utf-8")
    settings.BASE_DIR = tmp_path
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})

    with pytest.raises(ImproperlyConfigured, match="must contain non-empty strings"):
        AddonInstaller(LocalInstallerBackend()).desired_app_names()


def test_addon_installer_resolves_local_default(settings: Any) -> None:
    """``addon_installer()`` resolves the default ``local`` backend from the registry."""

    settings.ANGEE_ADDON_INSTALLER_BACKEND = "local"

    assert isinstance(addon_installer().backend, LocalInstallerBackend)


def test_install_refuses_project_yaml_when_installed_apps_is_overridden(
    tmp_path: Path, settings: Any
) -> None:
    """Editing an eclipsed YAML value cannot claim to have queued a change."""

    installer = _local_installer(tmp_path, settings)
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset()

    result = installer.install("example.demo")

    assert result.ok is False
    assert "not controlled by the project settings.yaml" in result.summary


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


def test_installer_check_reports_every_backend_fault_with_distinct_ids(
    settings: Any,
) -> None:
    """Import, subclass, and selected-key faults are all reported in one pass."""

    settings.ANGEE_ADDON_INSTALLER_BACKEND = "absent"
    settings.ANGEE_ADDON_INSTALLER_BACKEND_CLASSES = {
        "missing": "tests.test_addon_installer.MissingBackend",
        "wrong_base": "builtins.str",
    }

    issues = _check_installer_backends(None)

    assert [issue.id for issue in issues] == [
        "angee.platform.E002",
        "angee.platform.E003",
        "angee.platform.E004",
    ]
