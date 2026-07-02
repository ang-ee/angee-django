"""Regression coverage for operator stack templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
LOCAL_COPIER = ROOT / "templates" / "stacks" / "local" / "copier.yml"
LOCAL_TEMPLATE = ROOT / "templates" / "stacks" / "local" / "template" / "angee.yaml.jinja"


def _render_local_stack(*, frontend_mode: str) -> dict[str, Any]:
    """Render the local stack template enough for YAML contract tests."""

    text = _render_frontend_mode_branches(LOCAL_TEMPLATE.read_text(encoding="utf-8"), frontend_mode)
    replacements = {
        "base_image": "ghcr.io/ang-ee/django-angee:latest",
        "caddy_image": "caddy:2.9-alpine",
        "django_port": "8000",
        "instance_name": "angee-local",
        "operator_port": "9000",
        "ui_port": "5173",
        "web_image": "ghcr.io/ang-ee/angee-web:latest",
        "web_path": "web",
    }
    for key, value in replacements.items():
        text = text.replace(f"{{{{ {key} }}}}", value)
    assert "{{" not in text
    assert "{%" not in text
    rendered = yaml.safe_load(text)
    assert isinstance(rendered, dict)
    return rendered


def _render_frontend_mode_branches(text: str, frontend_mode: str) -> str:
    """Evaluate this template's simple frontend_mode if/elif/endif blocks."""

    frames: list[dict[str, bool]] = []
    output: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{% if frontend_mode =="):
            active = _condition_matches(stripped, frontend_mode) and _parent_active(frames)
            frames.append({"active": active, "matched": active, "parent": _parent_active(frames)})
            continue
        if stripped.startswith("{% elif frontend_mode =="):
            frame = frames[-1]
            active = frame["parent"] and not frame["matched"] and _condition_matches(stripped, frontend_mode)
            frame["active"] = active
            frame["matched"] = frame["matched"] or active
            continue
        if stripped == "{% endif %}":
            frames.pop()
            continue
        if _parent_active(frames):
            output.append(line)

    assert not frames
    return "\n".join(output) + "\n"


def _condition_matches(statement: str, frontend_mode: str) -> bool:
    return f'"{frontend_mode}"' in statement


def _parent_active(frames: list[dict[str, bool]]) -> bool:
    return all(frame["active"] for frame in frames)


def test_local_stack_frontend_mode_contract() -> None:
    manifest = yaml.safe_load(LOCAL_COPIER.read_text(encoding="utf-8"))

    assert manifest["frontend_mode"] == {
        "type": "str",
        "default": "caddy_static",
        "choices": ["caddy_static", "vite"],
        "help": (
            "Frontend ingress mode. caddy_static builds the SPA once and serves it through Caddy "
            "while proxying backend paths over the Docker network; vite keeps the legacy Vite dev "
            "server with direct host ports."
        ),
    }
    assert manifest["caddy_image"]["default"] == "caddy:2.9-alpine"


def test_local_stack_caddy_static_renders_single_public_frontend_ingress() -> None:
    stack = _render_local_stack(frontend_mode="caddy_static")

    assert "vite" not in stack["services"]
    assert "frontend-build" in stack["services"]
    assert "caddy" in stack["services"]
    assert "ports" not in stack["services"]["django"]
    assert stack["services"]["django"]["env"]["ANGEE_BUILTIN_MCP_URL"] == "http://django:8000/mcp"

    caddy = stack["services"]["caddy"]
    assert caddy["ports"] == ["5173:80"]
    assert caddy["after"] == ["django", "frontend-build"]
    assert set(caddy["after"]) <= set(stack["services"])
    caddyfile_command = caddy["command"][-1]
    assert "until [ -s /srv/project/web/dist/index.html ]" in caddyfile_command
    assert "reverse_proxy django:8000" in caddyfile_command
    assert "uri strip_prefix /operator" in caddyfile_command
    assert "reverse_proxy host.docker.internal:${ports.operator}" in caddyfile_command
    assert "root * /srv/project/web/dist" in caddyfile_command
    assert "try_files {path} /index.html" in caddyfile_command


def test_local_stack_vite_mode_preserves_legacy_direct_ports() -> None:
    stack = _render_local_stack(frontend_mode="vite")

    assert "jobs" not in stack
    assert "caddy" not in stack["services"]
    assert "vite" in stack["services"]
    assert stack["services"]["django"]["ports"] == ["8000:8000"]
    assert stack["services"]["vite"]["ports"] == ["5173:5173"]
    assert stack["services"]["vite"]["env"]["ANGEE_DJANGO_URL"] == "http://django:8000"
