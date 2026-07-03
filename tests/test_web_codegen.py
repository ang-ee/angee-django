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
