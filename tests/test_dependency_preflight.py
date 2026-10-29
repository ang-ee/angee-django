"""Dependency mutation delegates resolution to uv and guards publication/sync."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from angee.platform_integrate_operator import preflight


def test_project_view_preserves_native_workspace_and_sibling_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uv edits only copied host metadata; relative source identities survive intact."""
    if shutil.which("uv") is None:
        pytest.skip("uv is required for the native source-identity proof")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    host = tmp_path / "stack"
    member = host / "addons" / "member"
    sibling = tmp_path / "sibling"
    member.mkdir(parents=True)
    sibling.mkdir()
    added = tmp_path / "added"
    added.mkdir()
    (added / "pyproject.toml").write_text('[project]\nname="view-added"\nversion="0"\n')
    (member / "pyproject.toml").write_text('[project]\nname="view-member"\nversion="0"\n')
    (sibling / "pyproject.toml").write_text('[project]\nname="view-sibling"\nversion="0"\n')
    (host / "pyproject.toml").write_text('''# Preserve operator comments.
[project]
name="view-host"
version="0"
requires-python=">=3.12"
dependencies=["view-member", "view-sibling"]
[tool.uv.workspace]
members=["addons/*"]
[tool.uv.sources]
view-member={workspace=true}
view-sibling={path="../sibling",editable=true}
view-added={path="../added",editable=true}
''')
    environment = host / ".venv"
    preflight._uv(host, environment, "lock", "--offline")
    before = {p: p.read_bytes() for p in tmp_path.rglob("pyproject.toml")}
    original_lock = (host / "uv.lock").read_bytes()
    with preflight.project_view(host) as scratch:
        # Native dry-run proves the writable workspace owner before uv remove.
        preflight._plan(scratch, environment, ())
        preflight._uv(scratch, environment, "lock", "--offline")
        assert (scratch / "uv.lock").read_bytes() == original_lock
        preflight._uv(scratch, environment, "remove", "--no-sync", "--", "view-sibling")
        preflight._uv(scratch, environment, "add", "--no-sync", "--", "view-added")
        plan = preflight._plan(scratch, environment, ("--locked",))
        assert Path(plan["sync"]["environment"]["path"]) == environment
        assert "../sibling" not in (scratch / "uv.lock").read_text()
        assert (scratch / "pyproject.toml").read_text().startswith("# Preserve operator comments.")
        assert (host / "uv.lock").read_bytes() == original_lock
        assert all(path.read_bytes() == content for path, content in before.items())
        checked = {name: (scratch / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
        preflight._publish(host, {"pyproject.toml": before[host / "pyproject.toml"], "uv.lock": original_lock}, checked)
    # The identical checked bytes are valid at the authored root without relocking.
    preflight._plan(host, environment, ("--locked", "--offline"))
    assert all((host / name).read_bytes() == content for name, content in checked.items())
    assert not environment.exists()  # dry-run / --no-sync never creates a venv


def test_uv_removal_projection_excludes_replacements_and_rejects_unknown_actions() -> None:
    plan = {"sync": {"changes": [
        {"name": "retired", "action": "uninstalled"},
        {"name": "updated", "action": "uninstalled"},
        {"name": "updated", "action": "installed"},
    ]}}
    assert preflight.removal_set(plan) == ("retired",)
    plan["sync"]["changes"].append({"name": "opaque", "action": "unknown"})
    with pytest.raises(ValueError, match="Unsupported"):
        preflight.removal_set(plan)


@pytest.mark.parametrize("changed", ("pyproject.toml", "uv.lock"))
def test_publication_checks_both_files_before_replacing_either(tmp_path: Path, changed: str) -> None:
    before = {"pyproject.toml": b"old metadata", "uv.lock": b"old lock"}
    for name, content in before.items():
        (tmp_path / name).write_bytes(content)
    (tmp_path / changed).write_bytes(b"concurrent edit")
    with pytest.raises(RuntimeError, match="changed during"):
        preflight._publish(tmp_path, before, dict.fromkeys(before, b"new"))
    for name, content in before.items():
        assert (tmp_path / name).read_bytes() == (b"concurrent edit" if name == changed else content)


def test_failed_second_publication_restores_original_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = {"pyproject.toml": b"old metadata", "uv.lock": None}
    (tmp_path / "pyproject.toml").write_bytes(before["pyproject.toml"])
    native = preflight.atomic_write

    def fail_lock(path: Path, content: bytes) -> None:
        if path.name == "uv.lock":
            raise OSError("publication failure")
        native(path, content)

    monkeypatch.setattr(preflight, "atomic_write", fail_lock)
    with pytest.raises(OSError, match="publication failure"):
        preflight._publish(tmp_path, before, {"pyproject.toml": b"new metadata", "uv.lock": b"new lock"})
    assert (tmp_path / "pyproject.toml").read_bytes() == before["pyproject.toml"]
    assert not (tmp_path / "uv.lock").exists()


@pytest.mark.parametrize("environment_name", (".venv", "data/.venv-linux"))
@pytest.mark.parametrize("audit_refuses", (False, True))
def test_import_verdict_precedes_exact_lock_publication_and_real_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_refuses: bool, environment_name: str,
) -> None:
    """This is the orchestration boundary, not a substitute compiler audit proof."""
    selected_environment = tmp_path / environment_name
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(selected_environment))
    interpreter = selected_environment / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    before = {"pyproject.toml": b"original metadata", "uv.lock": b"original lock"}
    for name, content in before.items():
        (tmp_path / name).write_bytes(content)
    events: list[str] = []

    def uv(project: Path, environment: Path, *arguments: str) -> str:
        assert environment == selected_environment
        if arguments[0] == "remove":
            assert "--no-sync" in arguments
            assert project != tmp_path
            (project / "pyproject.toml").write_bytes(b"checked metadata")
            (project / "uv.lock").write_bytes(b"checked lock")
        elif "--dry-run" in arguments:
            return json.dumps({"dry_run": True, "project": {"workspace": {"path": str(project)}},
                "sync": {"environment": {"path": str(environment)},
                         "changes": [{"name": "retired", "action": "uninstalled"}]}})
        else:
            assert arguments == ("sync", "--locked", "--no-editable")
            assert (tmp_path / "uv.lock").read_bytes() == b"checked lock"
            assert (tmp_path / "pyproject.toml").read_bytes() == b"checked metadata"
            events.append("sync")
        return ""

    def audit(arguments: list[str], **kwargs: Any) -> None:
        assert arguments == [str(interpreter), "manage.py", "check_migrations", "--imports", "--removing", "retired"]
        assert kwargs["cwd"] == tmp_path
        assert all((tmp_path / name).read_bytes() == content for name, content in before.items())
        events.append("audit")
        if audit_refuses:
            raise subprocess.CalledProcessError(1, arguments)

    monkeypatch.setattr(preflight, "_uv", uv)
    monkeypatch.setattr(preflight.subprocess, "run", audit)
    if audit_refuses:
        with pytest.raises(subprocess.CalledProcessError):
            preflight.change_dependencies(tmp_path, "remove", "retired", no_editable=True)
        assert events == ["audit"]
        assert all((tmp_path / name).read_bytes() == content for name, content in before.items())
    else:
        assert preflight.change_dependencies(tmp_path, "remove", "retired", no_editable=True) == ("retired",)
        assert events == ["audit", "sync"]
