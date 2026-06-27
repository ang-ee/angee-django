"""Tests for operator-rendered service templates."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_claude_code_template_sets_claude_code_model_env() -> None:
    """Claude Code reads ANTHROPIC_MODEL, not the old CLAUDE_MODEL name."""

    text = (ROOT / "templates/services/claude-code/template/service.yaml.jinja").read_text()

    assert 'ANTHROPIC_MODEL: "{{ model }}"' in text
    assert "CLAUDE_MODEL" not in text


def test_service_templates_render_runtime_owned_auth_env() -> None:
    """Service templates consume the auth env block the agent runtime generates."""

    claude = (ROOT / "templates/services/claude-code/template/service.yaml.jinja").read_text()
    opencode = (ROOT / "templates/services/opencode/template/service.yaml.jinja").read_text()

    assert "{{ auth_env | safe }}" in claude
    assert "{{ auth_env | safe }}" in opencode
    # The provider-branching inputs and hardcoded env-var names are gone from both.
    assert "auth_mode" not in claude
    assert "secret_name" not in claude
    assert "ANTHROPIC_API_KEY" not in claude
    assert "OPENAI_API_KEY" not in opencode
    assert "GROQ_API_KEY" not in opencode
    assert "provider ==" not in opencode


def test_opencode_image_decodes_oauth_auth_store() -> None:
    """The opencode image decodes the base64 OAuth auth.json and gates the plugin on a build arg."""

    dockerfile = (ROOT / "templates/services/opencode/template/docker/Dockerfile").read_text()

    # The OAuth blob arrives base64 in ANGEE_OPENCODE_AUTH_B64 and is decoded into the store.
    assert "ANGEE_OPENCODE_AUTH_B64" in dockerfile
    assert "OPENCODE_AUTH_CONTENT" in dockerfile
    # The community auth plugin is opt-in via a build arg (empty by default — API-key only).
    assert 'ARG OPENCODE_ANTHROPIC_AUTH_PLUGIN=""' in dockerfile


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
