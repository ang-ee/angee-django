"""Native operator rendering of project inputs, including inherited stack mode."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("framework", ["source", "baked", None])
def test_native_project_render_resolves_framework_source_inputs(tmp_path: Path, framework: str | None) -> None:
    """A local stack supplies its mode; a standalone project needs no stack input."""

    operator = shutil.which("angee")
    if operator is None:
        pytest.skip("Native project rendering requires the Angee operator CLI.")
    target = tmp_path / "project"
    arguments = [operator, "init", str(target), "--yes"]
    if framework is not None:
        arguments.extend([
            "--template", str(ROOT / "templates/stacks/local"),
            "--input", f"framework={framework}",
        ])
    else:
        # init accepts stacks; a minimal native chain renders the project with
        # its public inputs and no deployment-mode input inherited from a stack.
        template = tmp_path / "standalone"
        (template / "template").mkdir(parents=True)
        (template / "copier.yml").write_text(yaml.safe_dump({
            "_subdirectory": "template",
            "_templates_suffix": ".jinja",
            "_angee": {
                "schema": 1,
                "kind": "stack",
                "name": "standalone-project",
                "chain": [{
                    "template": str(ROOT / "templates/projects/web"),
                    "inputs": {"framework_source_path": "sources/angee"},
                }],
            },
        }))
        (template / "template/angee.yaml.jinja").write_text("version: 1\nkind: stack\nname: standalone-project\n")
        arguments.extend(["--template", str(template)])
    result = subprocess.run(arguments, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    settings = yaml.safe_load((target / "settings.yaml").read_text())
    project = tomllib.loads((target / "pyproject.toml").read_text())
    if framework == "baked":
        assert settings["ANGEE_ADDON_DIRS"] == ["{BASE_DIR}/addons"]
        assert "sources" not in project["tool"]["uv"]
    else:
        assert settings["ANGEE_ADDON_DIRS"] == ["{BASE_DIR}/addons", "{BASE_DIR}/sources/angee/addons"]
        assert project["tool"]["uv"]["sources"]["django-angee"] == {"path": "sources/angee", "editable": True}
