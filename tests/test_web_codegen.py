"""Regression coverage for the frontend runtime codegen CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODEGEN = ROOT / "angee" / "web" / "app" / "bin" / "angee-web-codegen.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for frontend codegen")
def test_web_codegen_emits_extensioned_addon_entry_imports(tmp_path: Path) -> None:
    """Generated app imports point at concrete TypeScript entry files."""

    runtime = tmp_path / "runtime"
    web = tmp_path / "web"
    manifest_dir = runtime / "web"
    manifest_dir.mkdir(parents=True)
    web.mkdir()
    for package, extension in (("@demo/addon", ".tsx"), ("@demo/tools", ".ts")):
        entry_dir = web / "node_modules" / package / "src"
        entry_dir.mkdir(parents=True)
        (entry_dir / f"index{extension}").write_text("export default {};\n", encoding="utf-8")
    (web / "package.json").write_text(
        json.dumps({"dependencies": {"@demo/addon": "workspace:*", "@demo/tools": "workspace:*"}}),
        encoding="utf-8",
    )

    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "addonPackages": [
                    {"package": "@demo/addon", "sourceRoot": "src"},
                    {"package": "@demo/tools", "sourceRoot": "src"},
                ],
                "codegen": [],
                "documentRoots": [],
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(
        ["node", str(CODEGEN), "--runtime", str(runtime), "--web-root", str(web)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    app_module = (manifest_dir / "app.ts").read_text(encoding="utf-8")
    assert 'import addon0 from "../../web/node_modules/@demo/addon/src/index.tsx";' in app_module
    assert 'import addon1 from "../../web/node_modules/@demo/tools/src/index.ts";' in app_module


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for frontend codegen")
def test_web_codegen_rejects_enabled_addon_missing_host_dependency(tmp_path: Path) -> None:
    """An enabled addon's web package must be declared by the host web package."""

    runtime = tmp_path / "runtime"
    web = tmp_path / "web"
    manifest_dir = runtime / "web"
    manifest_dir.mkdir(parents=True)
    web.mkdir()
    (web / "package.json").write_text(json.dumps({"dependencies": {}}), encoding="utf-8")
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "addonPackages": [{"app": "example.billing", "package": "@example/billing", "sourceRoot": "src"}],
                "codegen": [],
                "documentRoots": [],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(CODEGEN), "--runtime", str(runtime), "--web-root", str(web)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert (
        f"example.billing declares frontend package @example/billing, "
        f"but it is missing from {web / 'package.json'}"
    ) in result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for frontend codegen")
def test_web_codegen_rejects_enabled_addon_unresolved_dependency(tmp_path: Path) -> None:
    """A declared addon dependency must resolve to an addon entry under node_modules."""

    runtime = tmp_path / "runtime"
    web = tmp_path / "web"
    manifest_dir = runtime / "web"
    manifest_dir.mkdir(parents=True)
    web.mkdir()
    (web / "package.json").write_text(
        json.dumps({"dependencies": {"@example/billing": "workspace:*"}}),
        encoding="utf-8",
    )
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "addonPackages": [{"app": "example.billing", "package": "@example/billing", "sourceRoot": "src"}],
                "codegen": [],
                "documentRoots": [],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(CODEGEN), "--runtime", str(runtime), "--web-root", str(web)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "example.billing declares frontend package @example/billing" in result.stderr
    assert f"cannot be resolved from {web / 'node_modules' / '@example/billing'}" in result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for frontend codegen")
def test_web_codegen_ignores_backend_only_addons(tmp_path: Path) -> None:
    """A backend-only composition has no host web dependency preflight."""

    runtime = tmp_path / "runtime"
    web = tmp_path / "web"
    manifest_dir = runtime / "web"
    manifest_dir.mkdir(parents=True)
    web.mkdir()
    (manifest_dir / "manifest.json").write_text(
        json.dumps({"schema": 1, "addonPackages": [], "codegen": [], "documentRoots": []}),
        encoding="utf-8",
    )

    subprocess.run(
        ["node", str(CODEGEN), "--runtime", str(runtime), "--web-root", str(web)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert (manifest_dir / "app.ts").is_file()
