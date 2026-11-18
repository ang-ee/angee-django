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
    instructions = (target / "AGENTS.md").read_text()
    assert "# Angee Project Host" in instructions
    assert "`INSTALLED_APPS` in `settings.yaml` is the roster" in instructions
    # A chained stack overlays its own root AGENTS.md, which carries the project body.
    assert ("# Angee Stack Root" in instructions) is (framework is not None)
    assert (target / "CLAUDE.md").readlink() == Path("AGENTS.md")
    settings = yaml.safe_load((target / "settings.yaml").read_text())
    project = tomllib.loads((target / "pyproject.toml").read_text())
    assert "ANGEE_ADDON_DIRS" not in settings
    assert settings["COMPOSE_OUTPUT_MODULE"] == "runtime"
    assert project["tool"]["uv"]["package"] is False
    assert "angee[postgres]>=3,<4" in project["project"]["dependencies"]
    if framework == "baked":
        assert "sources" not in project["tool"]["uv"]
    else:
        assert project["tool"]["uv"]["sources"]["angee"] == {"path": "sources/angee", "editable": True}


def test_native_staging_render_wires_repositories_and_their_deploy_keys(tmp_path: Path) -> None:
    """The operator renders a staging host from repositories alone, with no file edited after."""

    operator = shutil.which("angee")
    if operator is None:
        pytest.skip("Native stack rendering requires the Angee operator CLI.")
    usage = subprocess.run([operator, "init", "--help"], capture_output=True, text=True, timeout=30)
    if "--repository" not in usage.stdout + usage.stderr:
        pytest.skip("stacks/staging needs an operator with --repository.")
    target = tmp_path / "staging"
    result = subprocess.run(
        [
            operator, "init", str(target), "--yes",
            "--template", str(ROOT / "templates/stacks/staging"),
            "--input", "project_name=shop",
            "--input", "ingress_domain=shop.example.com",
            "--input", "roots=shop.storefront,shop.checkout",
            "--input", "settings_file=workspaces/src/angee-shop/deploy/settings.yaml",
            "--repository", "git@github.com:acme/angee-shop.git",
            "--extra-source", "acme/angee-catalog@release/1",
        ],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    stack = yaml.safe_load((target / "angee.yaml").read_text())
    assert stack["template"]["active"] == "stacks/staging"
    assert stack["ingress"]["domain"] == "shop.example.com"
    # An addon repository given by SSH URL: a slot, and its own deploy key.
    assert stack["sources"]["angee-shop"] == {
        "kind": "git",
        "repo": "git@github.com:acme/angee-shop.git",
        "default_ref": "main",
        "cache_path": "sources/angee-shop",
        "slot": True,
        "auth": {"ssh_key_secret": "deploy-key-angee-shop"},
    }
    # An extra source given as owner/name@ref: GitHub over https, no key.
    assert stack["sources"]["angee-catalog"] == {
        "kind": "git",
        "repo": "https://github.com/acme/angee-catalog.git",
        "default_ref": "release/1",
        "cache_path": "sources/angee-catalog",
        "slot": True,
    }
    assert stack["secrets"]["deploy-key-angee-shop"] == {"required": True}
    assert "deploy-key-angee-catalog" not in stack["secrets"]
    assert stack["secrets"]["admin-password"]["generated"] is True
    assert stack["jobs"]["provision"]["depends_on"] == ["compose", "makemigrations", "postgres"]
    assert "storybook" not in stack["services"]
    django_env = stack["services"]["django"]["env"]
    assert django_env["YAMLCONF_DEBUG"] == "false"
    assert django_env["YAMLCONF_CONFFILE"] == "/app/workspaces/src/angee-shop/deploy/settings.yaml"

    # The host installs the addon repository from its slot and enables the named roots.
    project = tomllib.loads((target / "pyproject.toml").read_text())
    assert "angee-shop" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["angee-shop"] == {"path": "workspaces/src/angee-shop", "editable": True}
    assert "angee-catalog" not in project["tool"]["uv"]["sources"]
    settings = yaml.safe_load((target / "settings.yaml").read_text())
    assert settings["INSTALLED_APPS"][-2:] == ["shop.storefront", "shop.checkout"]
    workspace = yaml.safe_load((target / "pnpm-workspace.yaml").read_text())
    assert {
        "workspaces/src/angee-shop/src/*/*/web", "workspaces/src/angee-catalog/src/*/*/web",
    } <= set(workspace["packages"])
    assert (target / "templates").readlink() == Path("workspaces/src/angee/templates")
    assert "deploy-key-angee-shop" in result.stdout + result.stderr
