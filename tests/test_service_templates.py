"""Tests for operator-rendered service templates."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_CODE_ROOT = ROOT / "templates/services/claude-code"
CLAUDE_CODE_COPIER = CLAUDE_CODE_ROOT / "copier.yml"
CLAUDE_CODE_DOCKERFILE = CLAUDE_CODE_ROOT / "template/docker/Dockerfile.jinja"
CLAUDE_CODE_SERVICE = CLAUDE_CODE_ROOT / "template/service.yaml.jinja"

BASE_DOCKERFILE = """# Claude Code ACP agent, exposed as a WebSocket by stdio-to-ws on :3007 (route.port).
# The operator's central Caddy terminates TLS + edge auth, so this image carries no
# Caddy and no token verifier — just the agent and the stdio<->ws bridge.
#
# The ACP claude adapter is @agentclientprotocol/claude-agent-acp (the older
# @zed-industries/claude-code-acp is deprecated/renamed). Pin it so the agent and the
# web client's @agentclientprotocol/sdk track the same protocol version.
FROM node:22-slim

COPY start-claude-code-acp.sh /usr/local/bin/start-claude-code-acp

RUN npm install -g @agentclientprotocol/claude-agent-acp@0.52.0 stdio-to-ws \\
    && chmod +x /usr/local/bin/start-claude-code-acp \\
    && mkdir -p /workspace /home/node/.claude \\
    && chown -R node:node /workspace /home/node/.claude

USER node
WORKDIR /workspace
EXPOSE 3007

CMD ["start-claude-code-acp"]
"""

BASE_SERVICE_TEMPLATE = """# One service entry appended to the outer stack by `service create`. The operator's
# central Caddy routes wss://{{ service_name }}.<domain>/ and forward-auths every
# upgrade against the daemon (route tokens are operator-minted), so this service runs
# a plain stdio<->ws bridge with no in-container auth. `route:` replaces `ports:` —
# a routed service publishes nothing and leases no host port.
services:
  {{ service_name }}:
    runtime: container
    build:
      # The operator installs the rendered docker/ at <root>/services/<name>/docker
      # (root is ANGEE_ROOT, e.g. .angee) and requires the build context to
      # resolve there, relative to the generated compose file at the root.
      context: ./services/{{ service_name }}/docker
    mounts:
      - "workspace://{{ workspace_name }}:/workspace"
    env:
{%- if auth_env %}
{{ auth_env | safe }}
{%- endif %}
{%- if mcp_env %}
{{ mcp_env | safe }}
{%- endif %}
      CLAUDE_PERMISSION_MODE: "{{ permission_mode }}"
{%- if model %}
      ANTHROPIC_MODEL: "{{ model }}"
{%- endif %}
    route:
      port: 3007
      auth: forward
"""


def _render_dev_flavor(text: str, *, dev: bool) -> str:
    """Evaluate only the dev-flavor blocks, including pongo2's left trim."""

    block = re.compile(
        r'\n{%- if flavor == "dev" %}\n(?P<body>.*?){%- endif %}',
        flags=re.DOTALL,
    )

    def replace(match: re.Match[str]) -> str:
        if not dev:
            return ""
        return "\n" + match.group("body").removesuffix("\n")

    return block.sub(replace, text)


def test_claude_code_template_declares_dev_flavor_and_git_identity_inputs() -> None:
    """Operator metadata and Copier questions expose the same input contract."""

    manifest = yaml.safe_load(CLAUDE_CODE_COPIER.read_text(encoding="utf-8"))

    for inputs in (manifest["_angee"]["inputs"], manifest):
        assert inputs["flavor"] == {
            "type": "str",
            "default": "base",
            "choices": ["base", "dev"],
        }
        assert inputs["git_user_name"] == {"type": "str", "default": "Angee Agent"}
        assert inputs["git_user_email"] == {
            "type": "str",
            "default": "agent@angee.local",
        }


def test_claude_code_dockerfile_dev_flavor_installs_developer_tools() -> None:
    """The base image is unchanged while dev adds the source-workspace toolchain."""

    old_path = CLAUDE_CODE_DOCKERFILE.with_name("Dockerfile")
    assert CLAUDE_CODE_DOCKERFILE.is_file()
    assert not old_path.exists()

    template = CLAUDE_CODE_DOCKERFILE.read_text(encoding="utf-8")
    base = _render_dev_flavor(template, dev=False)
    dev = _render_dev_flavor(template, dev=True)

    assert base == BASE_DOCKERFILE
    assert "apt-get" not in base

    for package in ("git", "openssh-client", "curl", "ca-certificates", "jq", "procps"):
        assert package in dev
    assert "https://cli.github.com/packages/githubcli-archive-keyring.gpg" in dev
    assert "signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg" in dev
    assert "sha256sum --check --strict" in dev
    assert "apt-get install -y --no-install-recommends gh" in dev
    assert "https://astral.sh/uv/install.sh" in dev
    assert "UV_UNMANAGED_INSTALL=/usr/local/bin" in dev
    assert "corepack enable" in dev


def test_claude_code_service_dev_flavor_mounts_sources_and_sets_git_identity() -> None:
    """Dev services can use worktree gitdirs and receive an explicit commit identity."""

    template = CLAUDE_CODE_SERVICE.read_text(encoding="utf-8")
    base = _render_dev_flavor(template, dev=False)
    dev = _render_dev_flavor(template, dev=True)

    assert base == BASE_SERVICE_TEMPLATE
    assert "{{ stack_root }}/sources" not in base
    assert '- "bind://{{ stack_root }}/sources:{{ stack_root }}/sources"' in dev
    assert 'GIT_AUTHOR_NAME: "{{ git_user_name }}"' in dev
    assert 'GIT_COMMITTER_NAME: "{{ git_user_name }}"' in dev
    assert 'GIT_AUTHOR_EMAIL: "{{ git_user_email }}"' in dev
    assert 'GIT_COMMITTER_EMAIL: "{{ git_user_email }}"' in dev


def test_claude_code_template_sets_claude_code_model_env() -> None:
    """Claude Code reads ANTHROPIC_MODEL, not the old CLAUDE_MODEL name."""

    text = CLAUDE_CODE_SERVICE.read_text()

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

    dockerfile = CLAUDE_CODE_DOCKERFILE.read_text()
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
    assert "command -v git >/dev/null 2>&1" in start_script
    assert "git config --global --add safe.directory '*'" in start_script
