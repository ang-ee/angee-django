"""Regression coverage for workspace template contracts."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEV_COPIER = ROOT / "templates" / "workspaces" / "dev" / "copier.yml"


def test_dev_workspace_requires_work_state_path() -> None:
    manifest = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))

    work_state_input = manifest["_angee"]["inputs"]["work_state_path"]
    assert work_state_input["type"] == "str"
    assert work_state_input["required"] is True
    assert "default" not in work_state_input
    assert "shared private work-state Git repository" in work_state_input["help"]

    copier_question = manifest["work_state_path"]
    assert copier_question["type"] == "str"
    assert "default" not in copier_question
    assert "shared private work-state Git repository" in copier_question["help"]


def test_dev_workspace_materializes_work_state_from_local_source() -> None:
    manifest = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))

    assert manifest["_angee"]["sources"]["work-state"] == {
        "source": "work-angee-django",
        "kind": "local",
        "path": "${inputs.work_state_path}",
        "subpath": ".work",
    }
