"""Contract tests for the documented Angee stack."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_stack_names_celery_not_procrastinate() -> None:
    """The locked task engine is Celery over Redis, not Procrastinate."""

    text = (ROOT / "docs" / "stack.md").read_text(encoding="utf-8")

    assert "Celery + Redis" in text
    assert "| Procrastinate |" not in text


def test_pyproject_uses_celery_and_keeps_only_the_channels_seam() -> None:
    """The wheel carries jobs and Channels while Redis fanout stays addon-owned."""

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(data["project"]["dependencies"])

    assert any(dependency.startswith("celery[redis]") for dependency in dependencies)
    assert any(dependency.startswith("channels>=") for dependency in dependencies)
    assert not any(dependency.startswith("channels-redis") for dependency in dependencies)
    assert not any("procrastinate" in dependency for dependency in dependencies)
