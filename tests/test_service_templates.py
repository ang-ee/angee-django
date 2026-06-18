"""Tests for operator-rendered service templates."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_claude_code_template_sets_claude_code_model_env() -> None:
    """Claude Code reads ANTHROPIC_MODEL, not the old CLAUDE_MODEL name."""

    text = (ROOT / "templates/services/claude-code/template/service.yaml.jinja").read_text()

    assert 'ANTHROPIC_MODEL: "{{ model }}"' in text
    assert "CLAUDE_MODEL" not in text


def test_claude_code_container_applies_model_env_to_settings() -> None:
    """The container pins Claude Code's Default model to ANTHROPIC_MODEL."""

    dockerfile = (ROOT / "templates/services/claude-code/template/docker/Dockerfile").read_text()
    start_script = (
        ROOT / "templates/services/claude-code/template/docker/start-claude-code-acp.sh"
    ).read_text()

    assert "COPY start-claude-code-acp.sh" in dockerfile
    assert 'CMD ["start-claude-code-acp"]' in dockerfile
    assert '"settings.json"' in start_script
    assert "ANTHROPIC_MODEL" in start_script
    assert "ANTHROPIC_CUSTOM_MODEL_OPTION" in start_script
    assert "ANTHROPIC_DEFAULT_OPUS_MODEL" in start_script
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" in start_script
    assert "availableModels: [model]" in start_script
    assert "enforceAvailableModels: true" in start_script
