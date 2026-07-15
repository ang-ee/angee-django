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


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for frontend codegen")
def test_web_codegen_resolves_addon_entry_and_documents_from_manifest_root(
    tmp_path: Path,
) -> None:
    """A composed workspace addon need not be a direct host dependency."""

    runtime = tmp_path / "runtime"
    web = tmp_path / "web"
    addon = tmp_path / "addon-web"
    manifest_dir = runtime / "web"
    manifest_dir.mkdir(parents=True)
    web.mkdir()
    (addon / "src").mkdir(parents=True)
    (addon / "src" / "index.tsx").write_text("export default {};\n", encoding="utf-8")
    (addon / "src" / "documents.demo.ts").write_text(
        'export const Demo = /* GraphQL */ `query Demo { ping }`;\n',
        encoding="utf-8",
    )

    schema_dir = web / "node_modules" / "@demo" / "schema" / "schema"
    schema_dir.mkdir(parents=True)
    (schema_dir / "demo.graphql").write_text(
        "type Query { ping: String! }\n",
        encoding="utf-8",
    )

    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "addonPackages": [
                    {
                        "package": "@demo/addon",
                        "root": "../../addon-web",
                        "sourceRoot": "src",
                    }
                ],
                "codegen": [
                    {
                        "schema": "demo",
                        "package": "@demo/schema",
                        "sdl": "schema/demo.graphql",
                        "documents": "documents.demo.ts",
                        "types": False,
                    }
                ],
                "documentRoots": [
                    {
                        "kind": "package",
                        "package": "@demo/addon",
                        "path": "node_modules/@demo/addon/src",
                    }
                ],
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
    generated_documents = (runtime / "gql" / "demo" / "graphql.ts").read_text(
        encoding="utf-8"
    )
    assert 'import addon0 from "../../addon-web/src/index.tsx";' in app_module
    assert "DemoDocument" in generated_documents
