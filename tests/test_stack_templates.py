"""Regression coverage for operator stack templates.

Both stack templates (``stacks/dev`` = process or docker mode, ``stacks/local`` =
docker mode) render from ONE shared manifest body
(``stacks/_shared/stack-body.yaml.jinja``): each ``angee.yaml.jinja`` is a thin
``{% set %}`` header that includes it. The mini-renderer below inlines that include,
then evaluates the template constructs the operator's pongo2 engine handles —
``{% set %}``, nested equality/bare-flag conditionals, and the celery
``{% for role in [...] %}`` loop — so the contract tests pin whatever the templates
compute, never a value re-derived here.
"""

from __future__ import annotations

import asyncio
import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.apps import AppConfig

from angee.addons import addon_manifest

ROOT = Path(__file__).resolve().parents[1]
ROOT_GITIGNORE = ROOT / ".gitignore"
TEMPLATES_README = ROOT / "templates" / "README.md"
LOCAL_COPIER = ROOT / "templates" / "stacks" / "local" / "copier.yml"
LOCAL_TEMPLATE = ROOT / "templates" / "stacks" / "local" / "template" / "angee.yaml.jinja"
LOCAL_STACK_GITIGNORE = ROOT / "templates" / "stacks" / "local" / "template" / ".gitignore.jinja"
LOCAL_AGENTS_TEMPLATE = ROOT / "templates" / "stacks" / "local" / "template" / "AGENTS.md.jinja"
LOCAL_CLAUDE_TEMPLATE = ROOT / "templates" / "stacks" / "local" / "template" / "CLAUDE.md"
DEV_COPIER = ROOT / "templates" / "stacks" / "dev" / "copier.yml"
DEV_TEMPLATE = ROOT / "templates" / "stacks" / "dev" / "template" / "angee.yaml.jinja"
DEV_PNPM_WORKSPACE = DEV_TEMPLATE.with_name("pnpm-workspace.yaml.jinja")
DEV_AGENTS_TEMPLATE = DEV_TEMPLATE.with_name("AGENTS.md.jinja")
DEV_CLAUDE_TEMPLATE = DEV_TEMPLATE.with_name("CLAUDE.md")
DEV_STACK_GITIGNORE = DEV_TEMPLATE.with_name(".gitignore.jinja")
DEV_PACKAGE_TEMPLATE = DEV_TEMPLATE.with_name("package.json.jinja")
DEV_TEMPLATES_SYMLINK = DEV_TEMPLATE.with_name("templates")
SHARED_BODY = ROOT / "templates" / "stacks" / "_shared" / "stack-body.yaml.jinja"
SHARED_AGENTS = ROOT / "templates" / "stacks" / "_shared" / "AGENTS.md.jinja"
PNPM_LOCK = ROOT / "pnpm-lock.yaml"
PROJECT_GITIGNORE = ROOT / "templates" / "projects" / "web" / "template" / ".gitignore.jinja"
PROJECT_PYPROJECT_TEMPLATE = ROOT / "templates" / "projects" / "web" / "template" / "pyproject.toml.jinja"
PROJECT_SETTINGS_TEMPLATE = ROOT / "templates" / "projects" / "web" / "template" / "settings.yaml.jinja"

# Services both stack templates render from the one shared body.
SHARED_SERVICES = {"operator", "postgres", "redis", "django", "celery-worker"}
DJANGO_READY = {
    "cmd": [
        "python",
        "-c",
        "import socket; socket.create_connection(('127.0.0.1', 8000), 1).close()",
    ],
    "interval": "5s",
    "timeout": "3s",
    "start_period": "30s",
    "retries": 180,
}
# The slim node image has neither wget nor curl, so the container probes with node.
FRONTEND_READY = {
    "cmd": [
        "node",
        "-e",
        "require('net').connect(5173, '127.0.0.1')"
        ".on('connect', () => process.exit(0)).on('error', () => process.exit(1))",
    ],
    "interval": "5s",
    "timeout": "3s",
    "start_period": "30s",
    "retries": 180,
}


def _command_texts(stack: dict[str, Any]) -> list[str]:
    """Return flattened rendered job/service commands for polling assertions."""

    commands: list[str] = []
    for section in ("jobs", "services"):
        for component in stack.get(section, {}).values():
            command = component.get("command")
            if command is None:
                continue
            commands.append(command if isinstance(command, str) else " ".join(map(str, command)))
    return commands


def _rendered_caddyfile(frontend: dict[str, Any], *, edge_cidrs: str = "192.0.2.0/24 198.51.100.0/24") -> str:
    """Return the Caddyfile emitted by the rendered service command."""

    command = frontend["command"][-1]
    caddyfile = command.partition("<<'CADDY'\n")[2].partition("\nCADDY")[0]
    assert caddyfile
    return caddyfile.replace("EDGE_PROXY_CIDRS", edge_cidrs)


# --- the mini-renderer ---------------------------------------------------------

_INCLUDE = re.compile(r'{%\s*include\s+"([^"]+)"(?:\s+with\s+(\w+)="([^"]*)")?\s*%}')
_JINJA_TAG = re.compile(r"{%\s*(.*?)\s*%}")
_CONDITIONAL_TAG = re.compile(r"{%\s*(if\s+.*?|elif\s+.*?|else|endif)\s*%}")


def _render_stack_manifest(
    manifest_path: Path,
    variables: dict[str, str],
) -> dict[str, Any]:
    """Render a wrapper manifest + its shared body into a YAML contract dict.

    Runs the template passes in dependency order: inline the shared-body include,
    strip comments, bind ``{% set %}`` variables, expand the celery ``{% for %}``
    loop, evaluate nested/inline conditionals, then substitute the remaining
    ``{{ var }}`` interpolations.
    """

    text = _inline_includes(manifest_path)
    text = _strip_jinja_comments(text)
    text = _render_jinja_set_tags(text, variables)
    text = _render_for_loops(text, variables)
    text = _render_conditionals(text, variables)
    for key, value in variables.items():
        text = text.replace(f"{{{{ {key} }}}}", value)
    assert "{{" not in text, text
    assert "{%" not in text, text
    rendered = yaml.safe_load(text)
    assert isinstance(rendered, dict)
    return rendered


def _inline_includes(manifest_path: Path) -> str:
    """Splice each ``{% include "rel" %}`` with the file at ``rel`` from the loader base.

    The operator's pongo2 loader resolves an include against its base directory —
    the template's ``_subdirectory`` root (``<template>/template/``) — NEVER the
    including file's own directory (copier-go renders file content ``FromString``,
    so the include has no origin path). The dev manifest sits one level below the
    subdirectory root; resolving file-relative here would pin the wrong contract.
    """

    base = _template_subdirectory(manifest_path)
    active: set[Path] = set()

    def inline(path: Path) -> str:
        resolved = path.resolve()
        if resolved in active:
            raise AssertionError(f"recursive template include: {resolved}")
        active.add(resolved)
        text = resolved.read_text(encoding="utf-8")

        def repl(match: re.Match[str]) -> str:
            included = inline(base / match.group(1))
            if match.group(2):
                included = re.sub(rf"\b{match.group(2)}\b", f'"{match.group(3)}"', included)
            return included

        rendered = _INCLUDE.sub(repl, text)
        active.remove(resolved)
        return rendered

    return inline(manifest_path)


def _template_subdirectory(manifest_path: Path) -> Path:
    """Return the template's ``_subdirectory`` root (the pongo2 loader base)."""

    for ancestor in manifest_path.parents:
        if ancestor.name == "template" and (ancestor.parent / "copier.yml").exists():
            return ancestor
    raise AssertionError(f"no template _subdirectory above {manifest_path}")


def _strip_jinja_comments(text: str) -> str:
    """Drop `{# … #}` comments, enforcing pongo2's single-line-comment constraint.

    pongo2 (the operator's renderer) rejects a comment spanning lines ("Newline not
    permitted in a single-line comment"), so a multi-line comment in a template is a
    render-breaking bug this renderer must refuse to paper over.
    """

    for match in re.finditer(r"{#.*?#}", text, flags=re.DOTALL):
        assert "\n" not in match.group(0), f"multi-line jinja comment breaks pongo2: {match.group(0)[:80]}..."
    return re.sub(r"{#.*?#}", "", text)


def _render_jinja_set_tags(text: str, variables: dict[str, str]) -> str:
    """Evaluate the wrapper header's `{% set %}` lines, binding into ``variables``.

    Handles both the plain ``{% set x = "v" %}`` line and the single-line
    ``{% if … %}{% set x = … %}{% elif … %}…{% else %}…{% endif %}`` source-path
    conditionals, so the mode, the address strings, and the derived source paths /
    ``uv_project`` flag all come straight from the template's own expressions.
    """

    output: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{%") and "{% set " in stripped:
            _apply_set_line(stripped, variables)
            continue
        output.append(line)
    return "\n".join(output)


def _apply_set_line(line: str, variables: dict[str, str]) -> None:
    """Walk one `{% if/elif/else/set/endif %}` line as a mini branch evaluator."""

    frames: list[dict[str, bool]] = []
    for body in _JINJA_TAG.findall(line):
        if body.startswith("if "):
            parent = _parent_active(frames)
            active = parent and _eval_condition(body[len("if ") :], variables)
            frames.append({"active": active, "matched": active, "parent": parent})
        elif body.startswith("elif "):
            frame = frames[-1]
            active = frame["parent"] and not frame["matched"] and _eval_condition(body[len("elif ") :], variables)
            frame["active"] = active
            frame["matched"] = frame["matched"] or active
        elif body == "else":
            frame = frames[-1]
            frame["active"] = frame["parent"] and not frame["matched"]
            frame["matched"] = True
        elif body == "endif":
            frames.pop()
        elif body.startswith("set ") and _parent_active(frames):
            name, _, expr = body[len("set ") :].partition("=")
            variables[name.strip()] = _eval_expr(expr, variables)
    assert not frames


def _render_conditionals(text: str, variables: dict[str, str]) -> str:
    """Evaluate nested equality/bare-flag blocks, including inline boundary tags."""

    frames: list[dict[str, bool]] = []
    output: list[str] = []
    cursor = 0
    for match in _CONDITIONAL_TAG.finditer(text):
        if _parent_active(frames):
            output.append(text[cursor : match.start()])
        body = match.group(1)
        if body.startswith("if "):
            parent = _parent_active(frames)
            active = parent and _eval_condition(body[len("if ") :], variables)
            frames.append({"active": active, "matched": active, "parent": parent})
        elif body.startswith("elif "):
            frame = frames[-1]
            active = frame["parent"] and not frame["matched"] and _eval_condition(body[len("elif ") :], variables)
            frame["active"] = active
            frame["matched"] = frame["matched"] or active
        elif body == "else":
            frame = frames[-1]
            frame["active"] = frame["parent"] and not frame["matched"]
            frame["matched"] = True
        else:
            frames.pop()
        cursor = match.end()
    if _parent_active(frames):
        output.append(text[cursor:])
    assert not frames
    return "".join(output)


def _parent_active(frames: list[dict[str, bool]]) -> bool:
    return all(frame["active"] for frame in frames)


def _render_for_loops(text: str, variables: dict[str, str]) -> str:
    """Expand the celery role loop, including the queue-worker extension.

    pongo2 has no list literals in expressions and takes Django-style (colon)
    filter args, so the template iterates a split string — optionally
    concatenated with the ``celery_queues`` input (``"…"|add:VAR|split:","``)
    and guarded by ``{% if role %}`` so the trailing comma's empty item is
    skipped. Per-role inline conditionals (``role == "…"`` with an optional
    ``{% else %}``, and the two-way ``role != … and role != …`` queue-args
    guard) resolve against each concrete item.
    """

    def resolve_role_conditionals(piece: str, item: str) -> str:
        piece = re.sub(
            r'{%\s*if\s+role\s*==\s*"([^"]*)"\s*%}(.*?)(?:{%\s*else\s*%}(.*?))?{%\s*endif\s*%}',
            lambda m: m.group(2) if item == m.group(1) else (m.group(3) or ""),
            piece,
            flags=re.DOTALL,
        )
        piece = re.sub(
            r'{%\s*if\s+role\s*!=\s*"([^"]*)"\s+and\s+role\s*!=\s*"([^"]*)"\s*%}(.*?){%\s*endif\s*%}',
            lambda m: m.group(3) if item not in (m.group(1), m.group(2)) else "",
            piece,
            flags=re.DOTALL,
        )
        return piece.replace("{{ role }}", item)

    def expand(match: re.Match[str]) -> str:
        literal, add_var, sep, body = match.groups()
        joined = literal + (variables.get(add_var, "") if add_var else "")
        guard = re.match(r"\s*{%\s*if\s+role\s*%}(.*){%\s*endif\s*%}\s*$", body, flags=re.DOTALL)
        inner = guard.group(1) if guard is not None else body
        return "".join(
            resolve_role_conditionals(inner, item.strip())
            for item in joined.split(sep)
            if item.strip() or guard is None
        )

    return re.sub(
        r'{%\s*for\s+role\s+in\s+"([^"]*)"(?:\|add:(\w+))?\|split:"([^"]*)"\s*%}(.*?){%\s*endfor\s*%}',
        expand,
        text,
        flags=re.DOTALL,
    )


def _eval_condition(condition: str, variables: dict[str, str]) -> bool:
    condition = condition.strip()
    if " or " in condition:
        return any(_eval_condition(part, variables) for part in condition.split(" or "))
    # pongo2 `and`: every conjunct must be truthy. Split before the == check so a
    # conjunct can itself be an equality test.
    if " and " in condition:
        return all(_eval_condition(part, variables) for part in condition.split(" and "))
    left, ne, right = condition.partition("!=")
    if ne:
        return _eval_operand(left, variables) != _eval_operand(right, variables)
    left, eq, right = condition.partition("==")
    if not eq:
        # Bare-flag condition (`{% if uv_project %}`): pongo2 truthiness — a
        # non-empty string is true.
        return bool(_eval_operand(left, variables))
    return _eval_operand(left, variables) == _eval_operand(right, variables)


def _eval_operand(operand: str, variables: dict[str, str]) -> str:
    operand = operand.strip().removeprefix("(").removesuffix(")").strip()
    base, sep, filter_name = operand.partition("|")
    value = _eval_expr(base, variables)
    if sep and filter_name.strip() == "first":
        return value[:1]
    return value


def _eval_expr(expr: str, variables: dict[str, str]) -> str:
    """Evaluate a ``{% set %}`` value: a literal, a variable, or pongo2 ``|add:`` concatenation."""

    return "".join(_eval_atom(atom, variables) for atom in expr.split("|add:"))


def _eval_atom(atom: str, variables: dict[str, str]) -> str:
    atom = atom.strip()
    if atom.startswith('"') and atom.endswith('"'):
        return atom[1:-1]
    return variables.get(atom, "")


# --- per-template renderers ----------------------------------------------------


def _render_local_stack(
    *,
    framework: str = "source",
    celery_queues: str = "",
    serve_mode: str = "development",
    serve_workers: int = 0,
    db_pool_max_size: int = 2,
    db_pool_timeout: int = 5,
    sentry_dsn: str = "",
    sentry_web_dsn: str = "",
) -> dict[str, Any]:
    """Render the docker-mode local stack enough for YAML contract tests."""

    variables = {
        "_src_path": "https://github.com/ang-ee/angee-django/tree/main/templates/stacks/local",
        "caddy_image": "caddy:2.9-alpine",
        "celery_queues": celery_queues,
        "django_image": "ghcr.io/ang-ee/django-angee-base:latest",
        "django_port": "8000",
        "framework": framework,
        "instance_name": "angee-local",
        "operator_port": "9000",
        "operator_image": "ghcr.io/ang-ee/angee-operator:latest",
        "process_compose_port": "8090",
        "runtime_mode": "docker",
        "serve_mode": serve_mode,
        "serve_workers": str(serve_workers),
        "db_pool_max_size": str(db_pool_max_size),
        "db_pool_timeout": str(db_pool_timeout),
        "sentry_dsn": sentry_dsn,
        "sentry_web_dsn": sentry_web_dsn,
        "ui_port": "5173",
        "web_image": "ghcr.io/ang-ee/angee-web:latest",
        "web_path": "web",
    }
    return _render_stack_manifest(LOCAL_TEMPLATE, variables)


def _render_dev_stack(
    *,
    project_path: str = ".",
    framework_path: str = "workspaces/src/angee",
    addons_profile: str = "base",
    include_arp: bool = False,
    work_state_source: str = "",
    work_state_repo: str = "",
    work_state_ref: str = "main",
    sources_home: str = "",
    celery_queues: str = "",
    enable_ollama: bool = False,
    ollama_port: str = "11434",
    ingress_domain: str = "localhost",
    _runtime_mode: str = "process",
    postgres_mode: str = "bundled",
    postgres_host: str = "127.0.0.1",
    postgres_db: str = "angee",
    postgres_user: str = "angee",
    postgres_port: int = 5433,
    redis_mode: str = "bundled",
    redis_host: str = "127.0.0.1",
    redis_port: int = 6379,
    redis_db: int = 0,
    redis_broker_db: int = 1,
    sentry_dsn: str = "",
    sentry_web_dsn: str = "",
    serve_mode: str = "development",
    serve_workers: int = 0,
    db_pool_max_size: int = 2,
    db_pool_timeout: int = 5,
) -> dict[str, Any]:
    """Render the process-mode framework-dev stack enough for YAML contract tests.

    ``project_path`` / ``framework_path`` model what the TEMPLATE receives: the
    operator (copierx.ResolvePathInputs) rewrites relative ``type: path`` inputs to
    be ANGEE_ROOT-relative in every render flow before the template runs. The
    project host IS the stack root (ANGEE_ROOT=.), so the default "." arrives as
    "." and the framework default arrives as the src workspace slot path; absolute
    inputs pass through verbatim.
    """

    variables = {
        "addons_profile": addons_profile,
        "include_arp": "true" if include_arp else "",
        "celery_queues": celery_queues,
        "caddy_image": "caddy:2.9-alpine",
        "django_image": "ghcr.io/ang-ee/django-angee-base:latest",
        "django_port": "8000",
        "edge_port": "80",
        "enable_ollama": "true" if enable_ollama else "",
        "framework_path": framework_path,
        "ingress_domain": ingress_domain,
        "node_image": "node:22-bookworm-slim",
        "ollama_port": ollama_port,
        "operator_port": "9000",
        "operator_image": "ghcr.io/ang-ee/angee-operator:latest",
        "playwright_image": "mcr.microsoft.com/playwright:v1.62.1-noble",
        "playwright_mcp_image": "mcr.microsoft.com/playwright/mcp:v0.0.76",
        "postgres_mode": postgres_mode,
        "postgres_host": postgres_host,
        "postgres_db": postgres_db,
        "postgres_user": postgres_user,
        "postgres_port": str(postgres_port),
        "process_compose_port": "8080",
        "project_name": "app",
        "project_path": project_path,
        "redis_mode": redis_mode,
        "redis_host": redis_host,
        "redis_port": str(redis_port),
        "redis_db": str(redis_db),
        "redis_broker_db": str(redis_broker_db),
        "sentry_dsn": sentry_dsn,
        "sentry_web_dsn": sentry_web_dsn,
        "runtime_mode": _runtime_mode,
        "serve_mode": serve_mode,
        "serve_workers": str(serve_workers),
        "db_pool_max_size": str(db_pool_max_size),
        "db_pool_timeout": str(db_pool_timeout),
        "sources_home": sources_home,
        "storybook_port": "6006",
        "ui_port": "5173",
        "web_path": "web",
        "work_state_source": work_state_source,
        "work_state_repo": work_state_repo,
        "work_state_ref": work_state_ref,
    }
    return _render_stack_manifest(DEV_TEMPLATE, variables)


def _render_dev_docker_stack(*, celery_queues: str = "", ingress_domain: str = "localhost") -> dict[str, Any]:
    """Render the Docker-mode framework-dev stack with every dev input present."""

    return _render_dev_stack(
        celery_queues=celery_queues,
        ingress_domain=ingress_domain,
        _runtime_mode="docker",
    )


def _render_project_settings(
    *,
    addon_installer_backend: str = "local",
    include_operator_installer: bool = False,
    addons_profile: str = "base",
    framework_workspace: bool = False,
    include_arp: bool = False,
) -> dict[str, Any]:
    """Render project settings enough for stack-owned contract tests."""

    text = PROJECT_SETTINGS_TEMPLATE.read_text(encoding="utf-8")
    text = _render_project_settings_conditionals(
        text,
        conditions={
            "{% if include_operator_installer %}": include_operator_installer,
            '{% if addon_installer_backend != "local" %}': addon_installer_backend != "local",
            '{% if addons_profile == "full" %}': addons_profile == "full",
            "{% if framework_workspace %}": framework_workspace,
            "{% if include_arp %}": include_arp,
        },
    )
    replacements = {
        "addon_installer_backend": addon_installer_backend,
        "addon_namespace": "angee_local",
        "project_name": "angee-local",
        "project_title": "Angee",
    }
    for key, value in replacements.items():
        text = text.replace(f"{{{{ {key} }}}}", value)
    assert "{{" not in text
    assert "{%" not in text
    rendered = yaml.safe_load(text)
    assert isinstance(rendered, dict)
    return rendered


def _render_project_pyproject(
    *,
    addons_profile: str = "base",
    framework_source_path: str = "",
) -> tuple[str, dict[str, Any]]:
    """Render the project Python manifest enough for dependency contract tests."""

    text = PROJECT_PYPROJECT_TEMPLATE.read_text(encoding="utf-8")
    text = _render_project_settings_conditionals(
        text,
        conditions={
            "{% if framework_source_path %}": bool(framework_source_path),
        },
    )
    replacements = {
        "addons_profile": addons_profile,
        "framework_source_path": framework_source_path,
        "project_name": "angee-local",
    }
    for key, value in replacements.items():
        text = text.replace(f"{{{{ {key} }}}}", value)
    assert "{{" not in text
    assert "{%" not in text
    return text, tomllib.loads(text)


def _render_project_settings_conditionals(text: str, *, conditions: dict[str, bool]) -> str:
    """Evaluate the settings-template line conditionals these tests need."""

    frames: list[bool] = []
    output: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in conditions:
            frames.append(conditions[stripped] and all(frames))
            continue
        if stripped == "{% endif %}":
            frames.pop()
            continue
        if all(frames):
            output.append(line)

    assert not frames
    return "\n".join(output) + "\n"


# --- shared-body contract ------------------------------------------------------


def test_both_stacks_render_from_one_shared_body() -> None:
    """Both wrappers include the single shared manifest body and share its services."""

    assert SHARED_BODY.exists()
    dev_text = DEV_TEMPLATE.read_text(encoding="utf-8")
    local_text = LOCAL_TEMPLATE.read_text(encoding="utf-8")

    # pongo2 resolves includes from the template's `_subdirectory` root (the loader
    # base), never the including file's dir — so BOTH templates use the same `../..`
    # hop count even though dev's manifest sits one level deeper.
    assert '{% include "../../_shared/stack-body.yaml.jinja" %}' in dev_text
    assert '{% include "../../_shared/stack-body.yaml.jinja" %}' in local_text
    dev_include = (_template_subdirectory(DEV_TEMPLATE) / "../../_shared/stack-body.yaml.jinja").resolve()
    local_include = (_template_subdirectory(LOCAL_TEMPLATE) / "../../_shared/stack-body.yaml.jinja").resolve()
    assert dev_include == SHARED_BODY == local_include

    dev = _render_dev_stack()
    dev_docker = _render_dev_docker_stack()
    local = _render_local_stack()
    assert SHARED_SERVICES <= set(dev["services"])
    assert SHARED_SERVICES <= set(dev_docker["services"])
    assert SHARED_SERVICES <= set(local["services"])


def test_both_stacks_render_shared_root_agent_instructions() -> None:
    """Every stack root teaches agents that it owns lifecycle and workspaces."""

    assert SHARED_AGENTS.exists()
    instructions = " ".join(SHARED_AGENTS.read_text(encoding="utf-8").split())
    for contract in (
        "The directory containing this file is `ANGEE_ROOT`.",
        "This stack is already initialized",
        "Do not run `angee init`",
        "`ANGEE_ROOT/workspaces/`",
        "Source checkouts are not stack roots",
        "The angee operator is this stack's monitoring and management service.",
        'angee --root "$ANGEE_ROOT" job run {{ restart_job }} --chained-restart',
        'angee --root "$ANGEE_ROOT" restart <service>...',
        "never run `uv sync` on the stack's venv while the stack runs",
        "docs/howto/getstarted.md#restart-the-running-stack",
    ):
        assert contract in instructions

    for agents_template, restart_job in ((DEV_AGENTS_TEMPLATE, "deps"), (LOCAL_AGENTS_TEMPLATE, "provision")):
        include = f'{{% include "../../_shared/AGENTS.md.jinja" with restart_job="{restart_job}" %}}'
        assert agents_template.read_text(encoding="utf-8").strip() == include

    for claude_template in (DEV_CLAUDE_TEMPLATE, LOCAL_CLAUDE_TEMPLATE):
        assert claude_template.is_symlink()
        assert claude_template.readlink() == Path("AGENTS.md")

    for copier_path in (DEV_COPIER, LOCAL_COPIER):
        copier = yaml.safe_load(copier_path.read_text(encoding="utf-8"))
        assert copier["_preserve_symlinks"] is True


# --- local (docker) contracts --------------------------------------------------


def test_local_stack_copier_contract() -> None:
    manifest = yaml.safe_load(LOCAL_COPIER.read_text(encoding="utf-8"))

    message = manifest["_message_after_copy"]
    assert "git clone https://github.com/ang-ee/angee-django sources/angee" in message
    assert "sources/angee/examples/addons" in message
    assert "angee dev" in manifest["_message_after_copy"]
    # The CLI shell recipe reads the token through the secrets backend owner —
    # never by hand-parsing .env, whose values are quoted.
    assert "angee secret reveal operator-token" in manifest["_message_after_copy"]
    assert "awk" not in manifest["_message_after_copy"]
    # frontend_mode / base_image are gone; framework + django_image replace them.
    assert "frontend_mode" not in manifest
    assert "base_image" not in manifest
    assert manifest["framework"]["default"] == "source"
    assert manifest["framework"]["choices"] == ["source", "baked"]
    assert manifest["django_image"]["default"] == "ghcr.io/ang-ee/django-angee-base:latest"
    assert manifest["caddy_image"]["default"] == "caddy:2.9-alpine"
    # 8090 ≠ the dev stack's 8080: side-by-side stacks must not share the
    # process-compose control port (a shared port lets one stack's down stop the other).
    assert manifest["process_compose_port"]["default"] == 8090
    assert "operator support for readiness and chained jobs" in TEMPLATES_README.read_text(encoding="utf-8")


def test_local_django_source_mode_bootstraps_fresh_host_dependencies() -> None:
    """Source containers project and install addon deps before importing Django apps."""

    stack = _render_local_stack(framework="source")
    django = stack["services"]["django"]
    provision = stack["jobs"]["provision"]

    assert django["image"] == "ghcr.io/ang-ee/django-angee-base:latest"
    command = django["command"][-1]
    assert "uv sync --frozen --inexact --extra postgres --project sources/angee" in command
    assert "uv sync --inexact" in command
    assert "exec python manage.py serve --host 0.0.0.0 --port 8000" in command
    provision_command = provision["command"][-1]
    assert "python -m angee.compose.bootstrap" in provision_command
    assert "python manage.py angee provision --bootstrap-admin" in provision_command
    assert provision["depends_on"] == ["postgres", "operator"]
    assert django["ready"] == DJANGO_READY
    # The PYTHONPATH hack is deleted — the editable link owns the framework on sys.path.
    assert "PYTHONPATH" not in django["env"]

    assert stack["sources"]["framework"]["path"] == "sources/angee"
    for service_name in ("celery-worker",):
        service = stack["services"][service_name]
        assert service["image"] == "ghcr.io/ang-ee/django-angee-base:latest"
        assert "PYTHONPATH" not in service["env"]
        celery_command = service["command"][-1]
        source_sync = "uv sync --frozen --inexact --extra postgres --project sources/angee"
        assert source_sync in celery_command
        assert celery_command.index(source_sync) < celery_command.index("uv sync --inexact && exec celery")
        assert service["after"] == ["provision", "redis"]


def test_local_django_baked_mode_skips_uv_sync() -> None:
    """Baked mode runs a code-baked image, so it never links a source checkout."""

    stack = _render_local_stack(framework="baked")
    django = stack["services"]["django"]

    assert "uv sync" not in django["command"][-1]
    assert "python manage.py angee provision --bootstrap-admin" in stack["jobs"]["provision"]["command"][-1]
    assert "framework" not in stack["sources"]
    for service_name in ("celery-worker",):
        assert "uv sync" not in stack["services"][service_name]["command"][-1]


def test_local_stack_renders_single_caddy_frontend_ingress() -> None:
    stack = _render_local_stack()

    assert "vite" not in stack["services"]
    assert set(stack["jobs"]) == {"provision", "operator-schema", "frontend-build"}
    assert "frontend-build" not in stack["services"]
    assert "caddy" in stack["services"]
    assert stack["template"]["active"].endswith("/templates/stacks/local")
    assert stack["template"]["active"] != "stacks/local"
    assert "ports" not in stack["services"]["django"]
    assert stack["services"]["django"]["env"]["ANGEE_BUILTIN_MCP_URL"] == "http://django:8000/mcp"
    assert stack["persist"]["pgdata"]["subpath"] == "./data/pgdata"
    assert stack["services"]["postgres"]["mounts"] == ["bind://./data/pgdata:/var/lib/postgresql/data"]
    assert "redis" in stack["services"]
    assert stack["services"]["django"]["env"]["REDIS_URL"] == "redis://redis:6379/0"
    assert stack["services"]["django"]["env"]["CELERY_BROKER_URL"] == "redis://redis:6379/1"
    assert "celery -A angee.jobs.celery:app worker" in stack["services"]["celery-worker"]["command"][-1]
    assert "celery -A angee.jobs.celery:app worker --beat" in stack["services"]["celery-worker"]["command"][-1]

    caddy = stack["services"]["caddy"]
    assert caddy["ports"] == ["5173:80"]
    assert caddy["after"] == ["frontend-build"]
    assert stack["services"]["django"]["after"] == ["provision", "caddy"]
    assert caddy["ready"] == {"http": {"port": 80, "path": "/"}}
    assert set(caddy["after"]) <= set(stack["services"]) | set(stack["jobs"])
    caddyfile_command = caddy["command"][-1]
    assert caddyfile_command.lstrip().startswith("cat >/etc/caddy/Caddyfile")
    assert "reverse_proxy django:8000" in caddyfile_command
    assert "uri strip_prefix /operator" in caddyfile_command
    assert "reverse_proxy operator:9000" in caddyfile_command
    assert "root * /srv/project/web/dist" in caddyfile_command
    assert "try_files {path} /index.html" in caddyfile_command

    frontend_command = stack["jobs"]["frontend-build"]["command"][-1]
    frontend_build = stack["jobs"]["frontend-build"]
    assert frontend_build["depends_on"] == ["provision", "operator-schema"]
    # Source-mode graft: overlay each @angee package src from the monorepo and
    # external bridge checkout, then symlink it into the mounted project.
    assert "project/sources/angee/packages" in frontend_command
    assert "project/sources/angee/addons/angee" in frontend_command
    assert "project/sources/angee-messaging-bridges/addons/angee" in frontend_command
    assert "fs.cpSync(srcDir,dstDir" in frontend_command
    assert 'path.join(root,"project/web/node_modules/@angee")' in frontend_command
    assert "fs.symlinkSync" in frontend_command
    assert "pnpm build" in frontend_command
    assert "tail -f /dev/null" not in frontend_command
    assert "runtime/schemas" not in frontend_command


def test_local_stack_uses_operator_backed_addon_installer() -> None:
    """Containerized local stacks edit project files through the host operator."""

    manifest = yaml.safe_load(LOCAL_COPIER.read_text(encoding="utf-8"))
    chain_inputs = manifest["_angee"]["chain"][0]["inputs"]
    stack = _render_local_stack()

    assert chain_inputs["addon_installer_backend"] == "operator"
    assert chain_inputs["include_operator_installer"] is True
    assert "operator-token" in stack["secrets"]
    assert stack["services"]["django"]["env"]["ANGEE_OPERATOR_TOKEN"] == "${secret.operator-token}"
    # Docker mode containerizes the daemon: the token rides argv via the secret
    # substitution (compose env-file interpolation), and the stack root mounts at
    # its host-identical path so in-container compose writes agree with the host.
    operator = stack["services"]["operator"]
    assert operator["runtime"] == "container"
    assert operator["command"][-2:] == ["--token", "${secret.operator-token}"]
    assert "bind://.:${stack.root}" in operator["mounts"]
    assert "bind:///var/run/docker.sock:/var/run/docker.sock" in operator["mounts"]
    assert operator["ports"] == ["127.0.0.1:${ports.operator}:9000"]


def test_project_template_can_render_operator_addon_installer_settings() -> None:
    """The local stack can opt into the operator installer bridge at project render time."""

    settings = _render_project_settings(addon_installer_backend="operator", include_operator_installer=True)

    assert "angee.platform_integrate_operator" in settings["INSTALLED_APPS"]
    assert settings["ANGEE_ADDON_INSTALLER_BACKEND"] == "operator"


def test_project_template_names_browser_cookies_per_project() -> None:
    """Concurrent localhost projects must not share Django's cookie namespace."""

    settings = _render_project_settings()

    assert settings["SESSION_COOKIE_NAME"] == "angee-local_sessionid"
    assert settings["CSRF_COOKIE_NAME"] == "angee-local_csrftoken"


def test_project_template_defaults_to_local_addon_installer() -> None:
    """Plain generated projects keep the dev/local writer unless a stack opts in."""

    settings = _render_project_settings(addon_installer_backend="local", include_operator_installer=False)

    assert "angee.platform_integrate_operator" not in settings["INSTALLED_APPS"]
    assert "ANGEE_ADDON_INSTALLER_BACKEND" not in settings


def test_project_web_host_leaves_home_and_confinement_to_the_composed_shell() -> None:
    """App roots declare their home and ``ANGEE_UI.shell`` selects the app; the rendered host passes neither."""

    project = ROOT / "templates" / "projects" / "web"
    answers = yaml.safe_load((project / "copier.yml").read_text())
    template = (project / "template" / "{{ web_path }}" / "src" / "main.tsx.jinja").read_text()

    assert not {"home", "confine_to"} & answers.keys()
    assert "home:" not in template
    assert "confineTo" not in template


def test_project_python_dependencies_bootstrap_the_generated_addon_group() -> None:
    """Fresh hosts document the pre-Django dependency bootstrap sequence."""

    base_text, base = _render_project_pyproject()
    full_text, full = _render_project_pyproject(
        addons_profile="full",
        framework_source_path="workspaces/src/angee",
    )

    for rendered, manifest in ((base_text, base), (full_text, full)):
        assert manifest["project"]["dependencies"] == ["django-angee[postgres]"]
        assert manifest["dependency-groups"]["addons"] == []
        assert manifest["tool"]["uv"]["default-groups"] == ["addons"]
        assert "uv run python -m angee.compose.bootstrap" in rendered
        assert rendered.count("GENERATED by angee build from the enabled addons' manifests — do not edit") == 1

    assert "extra-build-variables" not in base["tool"]["uv"]
    # No addon needs build variables any more (the Matrix bridge's python-olm
    # sdist chain is gone), so none renders for the full roster either.
    assert "extra-build-variables" not in full["tool"]["uv"]
    assert full["tool"]["uv"]["sources"]["django-angee"] == {
        "path": "workspaces/src/angee",
        "editable": True,
    }


def test_project_dependency_group_is_the_final_pyproject_table() -> None:
    """Stable final placement remains belt-and-braces generated-file hygiene."""

    for rendered, _manifest in (
        _render_project_pyproject(),
        _render_project_pyproject(
            addons_profile="full",
            framework_source_path="workspaces/src/angee",
        ),
    ):
        table_headers = re.findall(r"^\[[^]]+\]$", rendered, flags=re.MULTILINE)
        assert table_headers[-1] == "[dependency-groups]"


def test_project_template_addon_profiles_and_workspace_dirs() -> None:
    """`base` renders the consumer scaffold; `full` renders the whole platform
    composition; `framework_workspace` points the addon dirs at the src slots."""

    base = _render_project_settings()
    assert "example.notes" not in base["INSTALLED_APPS"]
    assert "angee.messaging_integrate_whatsapp" not in base["INSTALLED_APPS"]
    assert base["ANGEE_ADDON_DIRS"] == ["{BASE_DIR}/addons"]
    assert base["ANGEE_DATA_DIR"] == "{BASE_DIR}/data"

    full = _render_project_settings(addons_profile="full", framework_workspace=True)
    nexus = addon_manifest(AppConfig.create("angee.nexus"))
    assert nexus is not None
    assert set(nexus.depends_on) <= set(full["INSTALLED_APPS"])
    for app in (
        "angee.nexus",
        "angee.spaces",
        "angee.tags",
        "angee.agents",
        "angee.knowledge",
        "angee.workflows",
        "angee.work",
        "angee.intake",
        "angee.money",
        "angee.proposals",
        "angee.portfolio",
        "angee.messaging_integrate_whatsapp",
        "angee.messaging_integrate_telegram",
        "angee.messaging_integrate_discord",
        "example.notes",
    ):
        assert app in full["INSTALLED_APPS"]
    assert full["ANGEE_ADDON_DIRS"] == [
        "{BASE_DIR}/addons",
        "{BASE_DIR}/workspaces/src/angee/addons",
        "{BASE_DIR}/workspaces/src/angee-messaging-bridges/addons",
        "{BASE_DIR}/workspaces/src/angee/examples/addons",
    ]

    # base profile in the framework-workspace layout still finds the base addons.
    base_ws = _render_project_settings(framework_workspace=True)
    assert base_ws["ANGEE_ADDON_DIRS"] == [
        "{BASE_DIR}/addons",
        "{BASE_DIR}/workspaces/src/angee/addons",
    ]

    # include_arp adds only the discovery dir — never roster entries.
    arp = _render_project_settings(framework_workspace=True, include_arp=True)
    assert "{BASE_DIR}/workspaces/src/angee-arp/addons" in arp["ANGEE_ADDON_DIRS"]
    assert not any(app.startswith("arp.") for app in arp["INSTALLED_APPS"])


# --- dev (process) contracts ---------------------------------------------------


def test_dev_stack_declares_generated_graphql_type_dependencies() -> None:
    rendered = (
        DEV_PACKAGE_TEMPLATE.read_text(encoding="utf-8")
        .replace("{{ project_name }}", "example")
        .replace("{{ web_path }}", "web")
    )
    package = json.loads(rendered)
    assert package["dependencies"] == {
        "@angee/ui": "workspace:*",
        "@graphql-typed-document-node/core": "^3.2.0",
    }


def test_dev_stack_has_explicit_lifecycle_job_graph() -> None:
    """Framework dependency, provision, schema, and codegen jobs declare their order."""

    stack = _render_dev_stack()

    assert set(stack["jobs"]) == {"deps", "provision", "operator-schema", "codegen"}
    assert stack["jobs"]["provision"]["command"] == [
        "sh",
        "-c",
        "uv run python -m angee.compose.bootstrap && uv sync "
        "&& exec uv run python manage.py angee provision --demo --force-rebac",
    ]
    assert stack["jobs"]["provision"]["workdir"] == "source://app"
    assert stack["jobs"]["provision"]["env"]["ANGEE_PROJECT_DIR"] == "."
    assert stack["jobs"]["provision"]["depends_on"] == ["deps", "operator"]
    assert stack["jobs"]["operator-schema"]["depends_on"] == ["operator", "provision"]
    assert stack["jobs"]["codegen"]["depends_on"] == ["deps", "provision", "operator-schema"]
    # The serving processes now hang off provision, not the old resources/schema jobs.
    assert stack["services"]["django"]["after"] == ["provision"]
    assert stack["services"]["celery-worker"]["after"] == ["provision"]


def test_dev_stack_mounts_postgres_data_from_stack_root() -> None:
    stack = _render_dev_stack()

    assert stack["persist"]["pgdata"]["subpath"] == "./data/pgdata"
    assert stack["persist"]["app-data"]["subpath"] == "./data"
    assert stack["services"]["postgres"]["mounts"] == ["bind://./data/pgdata:/var/lib/postgresql/data"]
    assert stack["services"]["postgres"]["ports"] == ["${ports.postgres}:5432"]


@pytest.mark.parametrize(
    ("runtime_mode", "host", "port"),
    [("process", "127.0.0.1", 5433), ("docker", "host.docker.internal", 5544)],
)
def test_dev_stack_external_postgres_uses_server_without_stack_resources(
    runtime_mode: str, host: str, port: int
) -> None:
    stack = _render_dev_stack(
        _runtime_mode=runtime_mode,
        postgres_mode="external",
        postgres_host=host,
        postgres_port=port,
        postgres_user="app_role",
        postgres_db="app_db",
        celery_queues="reports,imports",
    )
    bundled = _render_dev_stack(_runtime_mode=runtime_mode, celery_queues="reports,imports")

    assert "postgres" not in stack["services"]
    assert "postgres" not in stack["ports"]
    assert "pgdata" not in stack["persist"]
    assert stack["secrets"]["db-password"] == {"required": True}
    assert "${ports.postgres}" not in json.dumps(stack)
    assert stack["jobs"]["provision"]["depends_on"] == (
        ["deps", "operator"] if runtime_mode == "process" else ["operator", "deps"]
    )
    assert stack["services"]["redis"] == bundled["services"]["redis"]
    assert stack["ports"].get("redis") == bundled["ports"].get("redis")

    database_nodes = {
        (section, name): node["env"]
        for section in ("services", "jobs")
        for name, node in stack[section].items()
        if "DATABASE_URL" in (node.get("env") or {})
    }
    bundled_nodes = {
        (section, name)
        for section in ("services", "jobs")
        for name, node in bundled[section].items()
        if "DATABASE_URL" in (node.get("env") or {})
    }
    assert set(database_nodes) == bundled_nodes
    for (section, name), env in database_nodes.items():
        assert env["DATABASE_URL"] == f"postgres://app_role:${{secret.db-password}}@{host}:{port}/app_db"
        for key in ("REDIS_URL", "CACHE_URL", "CELERY_BROKER_URL"):
            assert env[key] == bundled[section][name]["env"][key]


@pytest.mark.parametrize("runtime_mode", ["process", "docker"])
def test_dev_stack_bundled_postgres_custom_role_and_database_agree(runtime_mode: str) -> None:
    stack = _render_dev_stack(
        _runtime_mode=runtime_mode,
        postgres_user="app_role",
        postgres_db="app_db",
        postgres_host="db.example.test",
        postgres_port=5544,
    )

    assert stack["services"]["postgres"]["env"] == {
        "POSTGRES_USER": "app_role",
        "POSTGRES_DB": "app_db",
        "POSTGRES_PASSWORD": "${secret.db-password}",
    }
    address = "127.0.0.1:${ports.postgres}" if runtime_mode == "process" else "postgres:5432"
    assert stack["services"]["django"]["env"]["DATABASE_URL"] == (
        f"postgres://app_role:${{secret.db-password}}@{address}/app_db"
    )
    assert stack["secrets"]["db-password"] == {"generated": True, "length": 32}
    assert "pgdata" in stack["persist"]
    if runtime_mode == "process":
        assert stack["ports"]["postgres"]["value"] == 5544


@pytest.mark.parametrize(
    ("runtime_mode", "host", "port"),
    [("process", "127.0.0.1", 6379), ("docker", "host.docker.internal", 6380)],
)
def test_dev_stack_external_redis_uses_server_without_stack_resources(runtime_mode: str, host: str, port: int) -> None:
    stack = _render_dev_stack(
        _runtime_mode=runtime_mode,
        redis_mode="external",
        redis_host=host,
        redis_port=port,
        redis_db=4,
        redis_broker_db=5,
        celery_queues="reports,imports",
    )
    bundled = _render_dev_stack(_runtime_mode=runtime_mode, celery_queues="reports,imports")

    assert "redis" not in stack["services"]
    assert "redis" not in stack["ports"]
    assert "${ports.redis}" not in json.dumps(stack)
    assert stack["services"]["postgres"] == bundled["services"]["postgres"]
    for name, node in {**stack["services"], **stack["jobs"]}.items():
        assert "redis" not in (node.get("after") or []), name
    redis_nodes = [
        node["env"]
        for section in ("services", "jobs")
        for node in stack[section].values()
        if "REDIS_URL" in (node.get("env") or {})
    ]
    assert len(redis_nodes) == len(
        [
            node
            for section in ("services", "jobs")
            for node in bundled[section].values()
            if "REDIS_URL" in (node.get("env") or {})
        ]
    )
    for env in redis_nodes:
        assert env["REDIS_URL"] == env["CACHE_URL"] == f"redis://{host}:{port}/4"
        assert env["CELERY_BROKER_URL"] == f"redis://{host}:{port}/5"


@pytest.mark.parametrize("runtime_mode", ["process", "docker"])
def test_dev_stack_bundled_redis_custom_databases(runtime_mode: str) -> None:
    stack = _render_dev_stack(_runtime_mode=runtime_mode, redis_db=2, redis_broker_db=3)

    address = "127.0.0.1:${ports.redis}" if runtime_mode == "process" else "redis:6379"
    assert "redis" in stack["services"]
    assert stack["services"]["django"]["env"]["REDIS_URL"] == f"redis://{address}/2"
    assert stack["services"]["django"]["env"]["CELERY_BROKER_URL"] == f"redis://{address}/3"


def test_dev_stack_external_postgres_and_redis_together() -> None:
    stack = _render_dev_stack(
        postgres_mode="external",
        postgres_user="app_role",
        postgres_db="app_db",
        redis_mode="external",
        redis_db=6,
        redis_broker_db=7,
    )

    assert {"postgres", "redis"}.isdisjoint(stack["services"])
    assert {"postgres", "redis"}.isdisjoint(stack["ports"])
    env = stack["services"]["django"]["env"]
    assert env["DATABASE_URL"] == "postgres://app_role:${secret.db-password}@127.0.0.1:5433/app_db"
    assert env["REDIS_URL"] == "redis://127.0.0.1:6379/6"
    assert env["CELERY_BROKER_URL"] == "redis://127.0.0.1:6379/7"


def test_dev_stack_runs_redis_and_celery_services() -> None:
    stack = _render_dev_stack()

    assert "redis" in stack["services"]
    assert stack["services"]["redis"]["ports"] == ["${ports.redis}:6379"]
    assert stack["services"]["django"]["env"]["REDIS_URL"] == "redis://127.0.0.1:${ports.redis}/0"
    assert stack["services"]["django"]["env"]["CELERY_BROKER_URL"] == "redis://127.0.0.1:${ports.redis}/1"
    assert stack["services"]["celery-worker"]["env"]["CELERY_BROKER_URL"] == "redis://127.0.0.1:${ports.redis}/1"
    assert "celery" in stack["services"]["celery-worker"]["command"]
    assert "worker" in stack["services"]["celery-worker"]["command"]
    # One worker per stack embeds beat; there is no separate beat process.
    assert "--beat" in stack["services"]["celery-worker"]["command"]
    assert "celery-beat" not in stack["services"]


def test_dev_stack_ollama_is_opt_in_and_persistent() -> None:
    """The large shared Ollama service leaves no manifest entries until enabled."""

    manifest = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))
    assert manifest["enable_ollama"] == {
        "type": "bool",
        "default": False,
        "help": (
            "Run the shared Ollama container for local inference. Models are pulled manually "
            "and persist under the dev stack."
        ),
    }
    assert manifest["ollama_port"]["type"] == "int"
    assert manifest["ollama_port"]["default"] == 11434

    disabled = _render_dev_stack()
    assert "ollama" not in disabled["ports"]
    assert "ollama" not in disabled["persist"]
    assert "ollama" not in disabled["services"]
    assert "ollama" not in _render_local_stack()["services"]

    enabled = _render_dev_stack(enable_ollama=True, ollama_port="11435")
    assert enabled["ports"]["ollama"] == {"value": 11435, "export_env": "OLLAMA_PORT"}
    assert enabled["persist"]["ollama"] == {"subpath": "./data/ollama", "scope": "stack"}
    assert enabled["services"]["ollama"] == {
        "runtime": "container",
        "image": "ollama/ollama",
        "mounts": ["bind://./data/ollama:/root/.ollama"],
        "ports": ["${ports.ollama}:11434"],
    }


def test_dev_stack_keeps_the_process_only_frontend_services() -> None:
    stack = _render_dev_stack()

    assert "process_compose" in stack["ports"]
    assert "frontend" in stack["services"]
    assert "storybook" in stack["services"]
    assert "caddy" not in stack["services"]
    assert stack["services"]["frontend"]["command"] == ["pnpm", "--dir", "web", "dev"]
    assert stack["services"]["frontend"]["after"] == ["django", "operator", "codegen"]

    # Storybook runs in the STACK workspace (deps installed the monorepo
    # storybook package as a member) — a private install inside a slot would fork
    # dependency identities for every linked framework package.
    storybook = stack["services"]["storybook"]
    assert storybook["workdir"] == "source://app"
    assert "pnpm install" not in storybook["command"][-1]
    assert "exec pnpm --filter @angee/storybook dev --no-open" in storybook["command"][-1]


def test_dev_stack_runs_bare_uv_against_the_project_root_pyproject() -> None:
    """The project host IS the stack root; its pyproject owns framework resolution.

    The chained projects/web pyproject resolves django-angee editable from the
    framework slot (postgres extra baked into the dep) and pins uv's cache to the
    stack-owned caches/uv — so every uv invocation is bare: no ``--project``, no
    ``--extra``, no UV_CACHE_DIR override, in any layout. Provision uses a shell
    only to sequence the manifest bootstrap and its follow-up sync.
    """

    stack = _render_dev_stack()  # operator-rewritten defaults

    assert stack["sources"]["app"]["path"] == "."
    assert stack["sources"]["framework"]["path"] == "workspaces/src/angee"
    provision = stack["jobs"]["provision"]
    assert provision["workdir"] == "source://app"
    assert provision["command"][:2] == ["sh", "-c"]
    assert "--project" not in provision["command"][-1]
    assert "--extra" not in provision["command"][-1]
    assert "UV_CACHE_DIR" not in provision.get("env", {})

    for node in (
        stack["jobs"]["operator-schema"],
        stack["services"]["django"],
        stack["services"]["celery-worker"],
    ):
        assert node["workdir"] == "source://app"
        assert node["command"][:2] == ["uv", "run"]
        assert "--project" not in node["command"]
        assert "--extra" not in node["command"]
        assert "UV_CACHE_DIR" not in node.get("env", {})


def test_dev_stack_declares_the_framework_sources_and_the_src_workspace() -> None:
    """`angee dev` owns the whole bring-up: the manifest carries the source records
    and the src workspace declaration the two-command contract materializes."""

    stack = _render_dev_stack()

    assert set(stack["sources"]) == {"app", "framework", "angee"}
    assert stack["sources"]["angee"] == {
        "kind": "git",
        "repo": "https://github.com/ang-ee/angee-django.git",
        "default_ref": "main",
        "cache_path": "sources/angee",
    }

    assert stack["workspaces"]["src"] == {"template": "workspaces/src"}

    full = _render_dev_stack(addons_profile="full")
    assert set(full["sources"]) == {
        "app",
        "framework",
        "angee",
        "angee-messaging-bridges",
    }
    assert full["sources"]["angee-messaging-bridges"] == {
        "kind": "git",
        "repo": "https://github.com/ang-ee/angee-messaging-bridges.git",
        "default_ref": "main",
        "cache_path": "sources/angee-messaging-bridges",
    }

    # arpee is its own opt-in (a private product repo): absent from base AND
    # full profiles, declared only by include_arp.
    arp = _render_dev_stack(include_arp=True)
    assert set(arp["sources"]) == {"app", "framework", "angee", "angee-arp"}
    assert arp["sources"]["angee-arp"]["repo"] == "https://github.com/ang-ee/angee-arp.git"
    assert arp["sources"]["angee-arp"]["cache_path"] == "sources/angee-arp"

    # work_state_source names the .work slot's source; work_state_repo declares it.
    # Both set: the git source is rendered (location template-owned) AND bound.
    wired = _render_dev_stack(
        work_state_source="work-angee",
        work_state_repo="git@github.com:ang-ee/work-angee.git",
    )
    assert wired["workspaces"]["src"]["inputs"] == {"work_state_source": "work-angee"}
    # ...and every OTHER src-template workspace inherits the binding through the
    # stack's workspace_defaults, so `ws create --template src` needs no --input.
    # The same block always carries the stack's name as the workspace branch
    # prefix, so every workspace branch is `<stack>/<workspace>`.
    assert wired["workspace_defaults"] == {
        "workspaces/src": {"inputs": {"branch_prefix": "app", "work_state_source": "work-angee"}}
    }
    assert wired["sources"]["work-angee"] == {
        "kind": "git",
        "repo": "git@github.com:ang-ee/work-angee.git",
        "default_ref": "main",
        "cache_path": "sources/work/work-angee",
    }

    # work_state_ref overrides the source's default_ref.
    pinned = _render_dev_stack(
        work_state_source="work-angee",
        work_state_repo="git@github.com:ang-ee/work-angee.git",
        work_state_ref="trunk",
    )
    assert pinned["sources"]["work-angee"]["default_ref"] == "trunk"

    # Name only (repo left empty): the workspace binds the name, but the template
    # declares no source — the operator resolves it from a hand-declared source.
    name_only = _render_dev_stack(work_state_source="work-angee")
    assert name_only["workspaces"]["src"]["inputs"] == {"work_state_source": "work-angee"}
    assert name_only["workspace_defaults"] == {
        "workspaces/src": {"inputs": {"branch_prefix": "app", "work_state_source": "work-angee"}}
    }
    assert "work-angee" not in name_only["sources"]

    # Repo only (no name): nothing to key the source on, so no work-state wiring.
    repo_only = _render_dev_stack(work_state_repo="git@github.com:ang-ee/work-angee.git")
    assert "inputs" not in repo_only["workspaces"]["src"]
    # No work-state binding, but the branch-prefix default is unconditional.
    assert repo_only["workspace_defaults"] == {"workspaces/src": {"inputs": {"branch_prefix": "app"}}}
    assert set(repo_only["sources"]) == {"app", "framework", "angee"}

    # The local docker instance keeps its own source story (framework checkout at
    # sources/angee) — no framework git-source block, no workspace cut.
    local = _render_local_stack()
    assert set(local["sources"]) == {"app", "framework"}
    assert "workspaces" not in local
    assert "workspace_defaults" not in local


def test_dev_stack_shares_one_machine_wide_cache_per_repository() -> None:
    """sources_home moves every git cache to `<home>/<repository>` so all stacks
    on a machine cut worktrees from one clone; empty keeps per-stack caches.
    The stack name is always rendered as the workspace branch prefix so the
    shared cache never sees two stacks fighting over one branch name."""

    bare = _render_dev_stack()
    assert bare["sources"]["angee"]["cache_path"] == "sources/angee"
    assert bare["workspace_defaults"] == {"workspaces/src": {"inputs": {"branch_prefix": "app"}}}

    shared = _render_dev_stack(
        sources_home="/home/dev/sources",
        addons_profile="full",
        include_arp=True,
        work_state_source="work-angee",
        work_state_repo="git@github.com:ang-ee/work-angee.git",
    )
    assert shared["sources"]["angee"]["cache_path"] == "/home/dev/sources/angee-django"
    assert shared["sources"]["angee-messaging-bridges"]["cache_path"] == ("/home/dev/sources/angee-messaging-bridges")
    assert shared["sources"]["angee-arp"]["cache_path"] == "/home/dev/sources/angee-arp"
    assert shared["sources"]["work-angee"]["cache_path"] == "/home/dev/sources/work-angee"
    assert shared["workspace_defaults"]["workspaces/src"]["inputs"]["branch_prefix"] == "app"

    copier = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))
    assert copier["sources_home"]["type"] == "str"
    assert copier["sources_home"]["default"] == ""
    assert "<project_name>/<workspace>" in copier["project_name"]["help"]


def test_dev_stack_docker_mode_is_containerized_framework_dev() -> None:
    """Docker mode keeps the framework roster with containerized lifecycle jobs."""

    stack = _render_dev_docker_stack()

    assert set(stack["sources"]) == {"app", "framework", "angee"}
    assert stack["sources"]["angee"]["kind"] == "git"
    assert stack["sources"]["framework"]["path"] == "workspaces/src/angee"
    assert stack["workspaces"]["src"] == {"template": "workspaces/src"}

    assert set(stack["jobs"]) == {"deps", "provision", "operator-schema", "codegen"}
    operator_svc = stack["services"]["operator"]
    assert operator_svc["runtime"] == "container"
    assert operator_svc["image"] == "ghcr.io/ang-ee/angee-operator:latest"
    assert operator_svc["ports"] == ["${ports.operator}:9000"]
    assert "bind://.:${stack.root}" in operator_svc["mounts"]
    assert stack["services"]["django"]["env"]["ANGEE_OPERATOR_URL"] == "http://operator:9000"
    for name in ("django", "celery-worker", "frontend", "storybook"):
        assert stack["services"][name]["runtime"] == "container"

    django = stack["services"]["django"]
    django_command = django["command"][-1]
    assert "python manage.py runserver 0.0.0.0:8000" in django_command
    assert "angee provision" not in django_command
    assert "operator_schema" not in django_command
    assert stack["jobs"]["provision"]["depends_on"] == ["postgres", "operator", "deps"]
    assert stack["jobs"]["operator-schema"]["depends_on"] == ["operator", "provision"]
    assert stack["jobs"]["codegen"]["depends_on"] == ["deps", "provision", "operator-schema"]
    assert django["env"]["DATABASE_URL"] == "postgres://angee:${secret.db-password}@postgres:5432/angee"
    assert django["env"]["ANGEE_OPERATOR_URL"] == "http://operator:9000"
    assert django["ready"] == DJANGO_READY
    assert django["after"] == ["provision"]
    for name in ("celery-worker",):
        celery_command = stack["services"][name]["command"][-1]
        assert "uv sync --inexact && exec celery" in celery_command
        assert stack["services"][name]["after"] == ["provision", "redis"]

    frontend = stack["services"]["frontend"]
    assert frontend["image"] == "node:22-bookworm-slim"
    assert frontend["mounts"] == ["source://app:/app"]
    assert frontend["env"]["ANGEE_DJANGO_URL"] == "http://django:8000"
    frontend_command = frontend["command"][-1]
    assert frontend_command.startswith("corepack enable pnpm && exec pnpm --dir web dev")
    assert "pnpm install" not in frontend_command
    assert "runtime/schemas" not in frontend_command
    assert "codegen" not in frontend_command
    assert frontend["ready"] == FRONTEND_READY
    assert frontend["after"] == ["django", "codegen"]
    assert frontend["ports"] == ["${ports.ui}:5173"]
    assert "ANGEE_UI_ALLOWED_HOSTS" not in frontend["env"]

    storybook = stack["services"]["storybook"]
    assert storybook["image"] == "node:22-bookworm-slim"
    # Each container enables its own corepack shim — the frontend container's
    # `corepack enable` does not reach sibling containers.
    assert storybook["command"][-1].startswith("corepack enable pnpm;")
    assert "exec pnpm --filter @angee/storybook dev --no-open --host 0.0.0.0" in storybook["command"][-1]
    assert storybook["after"] == ["deps"]
    assert storybook["ports"] == ["${ports.storybook}:6006"]

    assert stack["ingress"] == {
        "type": "caddy",
        "routing": "path",
        "tls": "off",
        "domain": "localhost",
        "port": 80,
        "verify": "operator:9000",
    }
    assert "admin-password" not in stack["secrets"]
    assert "ports" not in stack["services"]["postgres"]
    assert "ports" not in stack["services"]["redis"]
    assert stack["ports"]["ui"] == {"value": 5173, "export_env": "ANGEE_UI_PORT"}
    assert stack["ports"]["storybook"] == {"value": 6006, "export_env": "STORYBOOK_PORT"}


def test_readiness_is_owned_by_long_running_http_and_django_services() -> None:
    """Rendered commands delegate dependency waits to manifest readiness probes."""

    process = _render_dev_stack(celery_queues="whatsapp")
    framework = _render_dev_docker_stack(celery_queues="whatsapp")
    instance = _render_local_stack(celery_queues="whatsapp")

    for stack in (process, framework, instance):
        for command in _command_texts(stack):
            assert "waiting for" not in command.lower()
            assert "until [ -s" not in command
        for service in stack["services"].values():
            command = service.get("command", [])
            command_text = command if isinstance(command, str) else " ".join(map(str, command))
            assert "django_migrations" not in command_text

    assert process["services"]["django"]["ready"]["tcp"] == {"port": 8000}
    assert {name for name, service in framework["services"].items() if "ready" in service} == {
        "django",
        "frontend",
    }
    assert {name for name, service in instance["services"].items() if "ready" in service} == {
        "django",
        "caddy",
    }

    assert framework["services"]["django"]["ready"] == DJANGO_READY
    assert framework["services"]["frontend"]["ready"] == FRONTEND_READY
    assert instance["services"]["django"]["ready"] == DJANGO_READY

    for name in ("celery-worker", "celery-whatsapp"):
        assert framework["services"][name]["after"] == ["provision", "redis"]
        assert instance["services"][name]["after"] == ["provision", "redis"]
    assert framework["services"]["frontend"]["after"] == ["django", "codegen"]
    assert framework["services"]["storybook"]["after"] == ["deps"]
    for name in ("playwright-server", "playwright-mcp"):
        assert framework["services"][name]["after"] == ["frontend"]
    assert instance["jobs"]["frontend-build"]["depends_on"] == ["provision", "operator-schema"]
    assert instance["services"]["caddy"]["after"] == ["frontend-build"]
    assert instance["services"]["django"]["after"] == ["provision", "caddy"]


def test_dev_stack_hostname_mode_secures_the_ux_ingress() -> None:
    """A public dev hostname routes the Docker UX through automatic edge TLS."""

    stack = _render_dev_docker_stack(ingress_domain="dev.example.com")

    assert stack["ingress"] == {
        "type": "caddy",
        "routing": "path",
        "tls": "auto",
        "domain": "dev.example.com",
        "verify": "operator:9000",
    }
    frontend = stack["services"]["frontend"]
    assert frontend["route"] == {"port": 5173, "path": "/", "auth": "none"}
    assert "ports" not in frontend
    assert frontend["env"]["ANGEE_UI_ALLOWED_HOSTS"] == "dev.example.com"
    assert stack["services"]["django"]["env"]["ANGEE_PUBLIC_ORIGIN"] == "https://dev.example.com"
    localhost_stack = _render_dev_docker_stack()
    assert "ANGEE_PUBLIC_ORIGIN" not in localhost_stack["services"]["django"]["env"]
    assert stack["services"]["playwright-server"]["route"] == {
        "port": 3100,
        "auth": "forward",
    }
    assert stack["services"]["playwright-mcp"]["route"] == {
        "port": 8931,
        "auth": "forward",
    }


def test_dev_stack_docker_mode_playwright_services_are_edge_routed() -> None:
    """Docker-mode Playwright endpoints are route-only and forward-authenticated."""

    stack = _render_dev_docker_stack()
    server = stack["services"]["playwright-server"]
    mcp = stack["services"]["playwright-mcp"]

    assert server["runtime"] == "container"
    assert server["image"] == "mcr.microsoft.com/playwright:v1.62.1-noble"
    assert server["route"] == {"port": 3100, "auth": "forward"}
    assert "ports" not in server
    assert server["command"][-1].startswith("corepack enable pnpm;")
    assert "exec pnpm --filter @angee/e2e exec playwright run-server" in server["command"][-1]
    assert server["after"] == ["frontend"]

    assert mcp["runtime"] == "container"
    assert mcp["image"] == "mcr.microsoft.com/playwright/mcp:v0.0.76"
    assert mcp["route"] == {"port": 8931, "auth": "forward"}
    assert "ports" not in mcp
    assert mcp["mounts"] == ["bind://./data/playwright:/data/profile"]
    assert mcp["command"] == [
        "--headless",
        "--browser",
        "chromium",
        "--no-sandbox",
        "--host",
        "0.0.0.0",
        "--port",
        "8931",
        # The edge bearer is the gate; the MCP host check would reject the
        # ingress domain Host header on this route-only, never-published port.
        "--allowed-hosts",
        "*",
        "--user-data-dir",
        "/data/profile",
    ]
    assert stack["persist"]["playwright-profile"] == {
        "subpath": "./data/playwright",
        "scope": "stack",
    }

    workspace = DEV_PNPM_WORKSPACE.read_text(encoding="utf-8")
    framework_globs = (
        '  - "workspaces/src/angee/packages/*"',
        '  - "workspaces/src/angee/addons/angee/*/web"',
    )
    full_profile_globs = (
        '  - "workspaces/src/angee/examples/addons/*/*/web"',
        '  - "workspaces/src/angee/examples/e2e"',
    )
    guard_index = workspace.index('{% if addons_profile == "full" %}')
    assert all(workspace.index(glob) < guard_index for glob in framework_globs)
    assert all(workspace.index(glob) > guard_index for glob in full_profile_globs)


def test_dev_stack_chains_the_project_host_with_the_framework_slot() -> None:
    """The dev chain renders the host at the stack root wired to the src slots."""

    manifest = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))
    chain = manifest["_angee"]["chain"][0]

    assert chain["template"] == "../../projects/web"
    inputs = chain["inputs"]
    assert inputs["framework_source_path"] == "${inputs.framework_path}"
    assert inputs["addons_profile"] == "${inputs.addons_profile}"
    assert inputs["framework_workspace"] is True
    assert inputs["addon_installer_backend"] == "operator"
    assert inputs["include_operator_installer"] is True

    assert manifest["framework_path"]["default"] == "workspaces/src/angee"
    assert manifest["project_path"]["default"] == "."
    assert manifest["addons_profile"]["choices"] == ["base", "full"]
    assert manifest["addons_profile"]["default"] == "base"
    assert manifest["work_state_source"]["default"] == ""
    assert manifest["work_state_repo"]["default"] == ""
    assert manifest["work_state_ref"]["default"] == "main"

    defaults = {
        "runtime_mode": "process",
        "django_image": "ghcr.io/ang-ee/django-angee-base:latest",
        "node_image": "node:22-bookworm-slim",
        "playwright_image": "mcr.microsoft.com/playwright:v1.62.1-noble",
        "playwright_mcp_image": "mcr.microsoft.com/playwright/mcp:v0.0.76",
        "ingress_domain": "localhost",
    }
    for name, default in defaults.items():
        assert manifest[name]["type"] == "str"
        assert manifest[name]["default"] == default
        assert manifest["_angee"]["inputs"][name]["default"] == default
    assert manifest["runtime_mode"]["choices"] == ["process", "docker"]
    assert manifest["_angee"]["inputs"]["runtime_mode"]["choices"] == ["process", "docker"]
    assert "ensure" not in manifest["_angee"]

    # The stack root's `templates` symlink resolves name-based template refs from
    # the framework source slot (the `framework_path` default). Copier emits the
    # link verbatim, so it names that default rather than the source cache, whose
    # location moves with `sources_home`.
    assert DEV_TEMPLATES_SYMLINK.is_symlink()
    assert str(DEV_TEMPLATES_SYMLINK.readlink()) == manifest["framework_path"]["default"] + "/templates"


def test_uv_caches_are_stack_owned() -> None:
    """Every stack pins uv's cache inside the stack.

    The dev stack needs no override — the rendered project pyproject pins
    ``cache-dir = "caches/uv"`` which uv resolves against the job CWD (the stack
    root). The docker instance overrides UV_CACHE_DIR to the container path of
    the same stack-owned dir.
    """

    dev = _render_dev_stack()
    for name in ("django", "celery-worker"):
        assert "UV_CACHE_DIR" not in dev["services"][name]["env"]

    dev_docker = _render_dev_docker_stack()
    local = _render_local_stack()
    for stack in (dev_docker, local):
        for name in ("django", "celery-worker"):
            assert stack["services"][name]["env"]["UV_CACHE_DIR"] == "/app/caches/uv"

    for gitignore_path in (LOCAL_STACK_GITIGNORE, DEV_STACK_GITIGNORE):
        assert "/caches/" in gitignore_path.read_text(encoding="utf-8")


def test_development_rendering_keeps_existing_serve_paths() -> None:
    for runtime in ("process", "docker"):
        stack = _render_dev_stack(_runtime_mode=runtime)
        assert "runserver" in " ".join(stack["services"]["django"]["command"])
        assert "frontend-build" not in stack["jobs"]
        assert "ANGEE_DB_POOL" not in stack["services"]["django"]["env"]
        assert "--workers" not in " ".join(stack["services"]["django"]["command"])
    local = _render_local_stack()
    assert local["services"]["django"]["runtime"] == "container"
    assert "--workers 1" in " ".join(local["services"]["django"]["command"])
    assert "ANGEE_DB_POOL" not in local["services"]["django"]["env"]
    assert local["services"]["caddy"]["after"] == ["frontend-build"]
    assert "/mcp /mcp/*" in local["services"]["caddy"]["command"][-1]
    assert "/acp/*" in local["services"]["caddy"]["command"][-1]


def test_serve_modes_cover_both_flavors_and_runtimes() -> None:
    """Production uses Docker; development retains both framework runtimes."""

    dev_copier = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))
    local_copier = yaml.safe_load(LOCAL_COPIER.read_text(encoding="utf-8"))
    for copier in (dev_copier, local_copier):
        assert copier["serve_mode"]["default"] == "development"
        assert copier["serve_mode"]["choices"] == ["development", "production"]
        assert copier["serve_workers"]["default"] == 0
        assert "serve_workers < 2" in copier["serve_workers"]["validator"]
        assert copier["db_pool_max_size"]["default"] == 2
        assert copier["db_pool_timeout"]["default"] == 5
    assert dev_copier["runtime_mode"]["choices"] == ["process", "docker"]
    assert "runtime_mode" not in local_copier
    assert 'serve_mode == "production" and runtime_mode == "process"' in dev_copier["serve_mode"]["validator"]
    assert "Production serving requires runtime_mode: docker." in dev_copier["serve_mode"]["validator"]
    assert dev_copier["_angee"]["inputs"]["serve_mode"] == dev_copier["serve_mode"]
    assert dev_copier["_angee"]["inputs"]["serve_workers"] == dev_copier["serve_workers"]
    assert dev_copier["_angee"]["inputs"]["db_pool_max_size"] == dev_copier["db_pool_max_size"]
    assert dev_copier["_angee"]["inputs"]["db_pool_timeout"] == dev_copier["db_pool_timeout"]

    cases = (
        ("framework", "process", "development"),
        ("framework", "docker", "development"),
        ("framework", "docker", "production"),
        ("instance", "docker", "development"),
        ("instance", "docker", "production"),
    )
    for flavor, runtime_mode, serve_mode in cases:
        stack = (
            _render_dev_stack(_runtime_mode=runtime_mode, serve_mode=serve_mode)
            if flavor == "framework"
            else _render_local_stack(serve_mode=serve_mode)
        )
        django = stack["services"]["django"]
        command = " ".join(django["command"])
        assert "ANGEE_SERVE_MODE" not in django["env"]
        if serve_mode == "production":
            assert "manage.py serve" in command
            assert "--workers 0" in command
            assert "--production" in command
            assert "runserver" not in command
            assert django["env"]["ANGEE_DB_POOL"] == "true"
            assert django["env"]["ANGEE_DB_POOL_MAX_SIZE"] == "2"
            assert django["env"]["ANGEE_DB_POOL_TIMEOUT"] == "5"
            assert "ANGEE_DB_POOL" not in stack["services"]["celery-worker"]["env"]
            assert "ANGEE_DB_POOL" not in stack["jobs"]["provision"]["env"]
            frontend = stack["services"]["frontend" if flavor == "framework" else "caddy"]
            caddyfile = frontend["command"][-1]
            assert "file_server" in caddyfile
            assert "try_files {path} /index.html" in caddyfile
            assert "/mcp /mcp/*" in caddyfile
            assert "/acp/*" in caddyfile
            assert "@operator_graphql path /operator/graphql /operator/graphql/*" in caddyfile
            assert "uri strip_prefix /operator" in caddyfile
            assert "@operator_logs" not in caddyfile
            assert caddyfile.index("handle @operator_graphql") < caddyfile.index("handle {")
            assert "/operator/*" not in caddyfile
            assert frontend["after"][0] == "frontend-build"
            assert django["after"] == ["provision", "frontend" if flavor == "framework" else "caddy"]
            assert "--allowed-origin" in " ".join(stack["services"]["operator"]["command"])
            assert '--forwarded-allow-ips "$$TRUSTED_PROXY_CIDR"' in command
            if flavor == "framework":
                assert stack["jobs"]["frontend-build"]["depends_on"] == ["codegen"]
                assert "pnpm --dir web build" in " ".join(stack["jobs"]["frontend-build"]["command"])
        elif flavor == "framework":
            assert "runserver" in command
            assert "frontend-build" not in stack["jobs"]


def test_process_render_is_development_even_when_serve_validator_is_bypassed() -> None:
    assert _render_dev_stack(_runtime_mode="process", serve_mode="production") == _render_dev_stack(
        _runtime_mode="process",
        serve_mode="development",
    )


def test_production_serve_workers_use_cpu_count_and_reject_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from django.core.management.base import CommandError

    from angee.compose.management.commands import serve

    started: list[dict[str, Any]] = []
    monkeypatch.setattr(serve.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(serve.uvicorn, "run", lambda _app, **kwargs: started.append(kwargs))
    options = {"workers": 0, "production": True, "host": "0.0.0.0", "port": 8000, "forwarded_allow_ips": "10.0.0.2"}
    serve.Command().handle(**options)
    assert started[0]["workers"] == 8
    assert started[0]["forwarded_allow_ips"] == "10.0.0.2"
    with pytest.raises(CommandError, match="at least two"):
        serve.Command().handle(**{**options, "workers": 1})


def test_production_pool_inputs_reach_only_django() -> None:
    stack = _render_dev_stack(
        _runtime_mode="docker",
        serve_mode="production",
        db_pool_max_size=3,
        db_pool_timeout=7,
    )
    assert stack["services"]["django"]["env"]["ANGEE_DB_POOL_MAX_SIZE"] == "3"
    assert stack["services"]["django"]["env"]["ANGEE_DB_POOL_TIMEOUT"] == "7"
    assert all("ANGEE_DB_POOL" not in job["env"] for job in stack["jobs"].values() if "env" in job)
    assert "ANGEE_DB_POOL" not in stack["services"]["celery-worker"]["env"]


def test_public_production_frontend_routes_after_operator_graphql() -> None:
    stack = _render_dev_stack(
        _runtime_mode="docker",
        serve_mode="production",
        ingress_domain="app.example.test",
    )
    frontend = stack["services"]["frontend"]
    assert frontend["route"] == {"port": 80, "path": "/", "auth": "none"}
    assert "ports" not in frontend
    assert "--allowed-origin https://app.example.test" in " ".join(stack["services"]["operator"]["command"])
    assert stack["services"]["django"]["env"]["ANGEE_PUBLIC_ORIGIN"] == "https://app.example.test"
    command = frontend["command"][-1]
    assert command.index("handle @operator_graphql") < command.index("handle {")
    assert "reverse_proxy django:8000" in command
    caddyfile = _rendered_caddyfile(frontend)
    assert "trusted_proxies static 192.0.2.0/24 198.51.100.0/24\n    trusted_proxies_strict" in caddyfile
    assert "until EDGE_IP=$$(getent hosts edge" in command
    assert "EDGE_PROXY_CIDRS=$$(ip -4 route | awk" in command
    assert 'sed -i "s|EDGE_PROXY_CIDRS|$$EDGE_PROXY_CIDRS|"' in command
    assert "header_up X-Forwarded-For {client_ip}" in caddyfile
    assert "header_up X-Forwarded-Proto" not in caddyfile


def test_forwarded_headers_reach_django_without_client_spoofing() -> None:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    stack = _render_dev_stack(
        _runtime_mode="docker",
        serve_mode="production",
        ingress_domain="app.example.test",
    )
    command = stack["services"]["django"]["command"][-1]
    assert 'socket.gethostbyname("frontend")' in command
    assert 'open("/proc/net/route")' in command
    match = re.search(r'--forwarded-allow-ips "([^"]+)"', command)
    assert match is not None
    assert match.group(1) == "$$TRUSTED_PROXY_CIDR"
    trusted = match.group(1).replace("$$TRUSTED_PROXY_CIDR", "192.0.2.0/24")
    caddyfile = _rendered_caddyfile(stack["services"]["frontend"])
    assert "trusted_proxies_strict" in caddyfile
    assert "header_up X-Forwarded-For {client_ip}" in caddyfile

    seen: list[tuple[tuple[str, int], str]] = []

    async def app(scope: dict[str, Any], _receive: Any, _send: Any) -> None:
        seen.append((scope["client"], scope["scheme"]))

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(_message: dict[str, Any]) -> None:
        pass

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
    for peer, forwarded_for, forwarded_proto in (
        ("192.0.2.20", "203.0.113.8", "https"),
        ("192.0.2.21", "198.51.100.9, 203.0.113.8", "https"),
        ("198.51.100.9", "198.51.100.9", "https"),
    ):
        scope: dict[str, Any] = {
            "type": "http",
            "client": (peer, 4123),
            "scheme": "http",
            "headers": [
                (b"x-forwarded-for", forwarded_for.encode()),
                (b"x-forwarded-proto", forwarded_proto.encode()),
            ],
        }
        asyncio.run(middleware(scope, receive, send))
    assert seen == [
        (("203.0.113.8", 0), "https"),
        (("203.0.113.8", 0), "https"),
        (("198.51.100.9", 4123), "http"),
    ]


def test_localhost_production_frontend_needs_no_edge_lookup() -> None:
    stack = _render_dev_stack(_runtime_mode="docker", serve_mode="production")
    command = stack["services"]["frontend"]["command"][-1]
    assert "getent hosts edge" not in command
    assert "trusted_proxies" not in command
    assert "header_up X-Forwarded-For {client_ip}" in command


def test_playwright_image_matches_e2e_lock_version() -> None:
    lock = yaml.safe_load(PNPM_LOCK.read_text(encoding="utf-8"))
    version = lock["importers"]["packages/e2e"]["devDependencies"]["@playwright/test"]["version"]
    image = f"mcr.microsoft.com/playwright:v{version}-noble"
    copier = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))
    assert copier["playwright_image"]["default"] == image
    assert copier["_angee"]["inputs"]["playwright_image"]["default"] == image


def test_every_stack_leases_its_process_compose_control_port() -> None:
    """All three cells declare ports.process_compose — the operator daemon is a
    runtime:local service in every mode, and a stack without an explicit lease
    falls back to the proccompose backend's shared default port, letting one
    stack's `angee down` tear down its neighbor's local processes (observed live:
    a docker-mode scratch stack stopped the resident dev stack's django/vite)."""

    assert _render_dev_stack()["ports"]["process_compose"]["value"] == 8080
    assert _render_dev_docker_stack()["ports"]["process_compose"]["value"] == 8080
    assert _render_local_stack()["ports"]["process_compose"]["value"] == 8090


def test_secret_key_is_mode_invariant() -> None:
    """Both modes declare the secret-key secret and run Django on it.

    Encrypted-at-rest fields (angee.base EncryptedField) derive their Fernet key
    from SECRET_KEY, so re-rendering a stack from process to docker (or back)
    must never rotate the effective key: both modes declare the same generated
    `secret-key` (the env-file backend reuses the existing .env value) and pin
    YAMLCONF_SECRET_KEY on every Django-running node.
    """

    dev = _render_dev_stack()
    dev_docker = _render_dev_docker_stack()
    local = _render_local_stack()
    assert "secret-key" in dev["secrets"]
    assert "secret-key" in dev_docker["secrets"]
    assert "secret-key" in local["secrets"]
    for stack in (dev, dev_docker, local):
        for name in ("django", "celery-worker"):
            assert stack["services"][name]["env"]["YAMLCONF_SECRET_KEY"] == "${secret.secret-key}"
    assert dev["jobs"]["provision"]["env"]["YAMLCONF_SECRET_KEY"] == "${secret.secret-key}"


def test_python_nodes_share_runtime_environment_and_restart_entry() -> None:
    """Every Python job/service receives the shared operator and application environment."""

    for stack, restart_job in (
        (_render_dev_stack(), "deps"),
        (_render_dev_docker_stack(), "deps"),
        (_render_local_stack(), "provision"),
    ):
        nodes = [
            stack["jobs"]["provision"],
            stack["jobs"]["operator-schema"],
            stack["services"]["django"],
            stack["services"]["celery-worker"],
        ]
        for node in nodes:
            assert node["env"]["ANGEE_OPERATOR_RESTART_JOB"] == restart_job
            assert node["env"]["ANGEE_OPERATOR_TOKEN"] == "${secret.operator-token}"
            assert node["env"]["YAMLCONF_SECRET_KEY"] == "${secret.secret-key}"


def test_stack_agent_instructions_name_the_rendered_restart_job() -> None:
    """The root AGENTS.md restart command runs the job the manifest declares as the restart entry."""

    for agents_template, stack in (
        (DEV_AGENTS_TEMPLATE, _render_dev_stack()),
        (LOCAL_AGENTS_TEMPLATE, _render_local_stack()),
    ):
        match = re.search(r'with restart_job="([^"]+)"', agents_template.read_text(encoding="utf-8"))
        assert match is not None, agents_template
        restart_job = match.group(1)
        assert restart_job in stack["jobs"]
        assert stack["services"]["django"]["env"]["ANGEE_OPERATOR_RESTART_JOB"] == restart_job


def test_dev_stack_keeps_absolute_source_paths_verbatim() -> None:
    """Absolute copier inputs are kept as-is (neither `../`-prefixed nor collapsed)."""

    stack = _render_dev_stack(project_path="/srv/project", framework_path="/opt/angee")

    assert stack["sources"]["app"]["path"] == "/srv/project"
    assert stack["sources"]["framework"]["path"] == "/opt/angee"
    # The rendered pyproject (whose framework_source_path follows framework_path)
    # owns resolution even for an external checkout — commands stay bare `uv run`.
    assert stack["jobs"]["provision"]["command"][:2] == ["sh", "-c"]
    assert "--project" not in stack["jobs"]["provision"]["command"][-1]


def test_dev_stack_keeps_stack_answers_separate_from_workspace_answers() -> None:
    manifest = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))

    assert manifest["_answers_file"] == ".copier-answers.stack.yml"
    for stack in (_render_dev_stack(), _render_dev_docker_stack()):
        assert stack["template"]["answers_file"] == ".copier-answers.stack.yml"


def test_dev_stack_prunes_dead_playwright_inputs() -> None:
    """Playwright returns as Docker-mode services, never host-port/browser inputs."""

    manifest = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))

    assert "playwright_port" not in manifest
    assert "playwright_browser" not in manifest
    assert "process_compose_port" in manifest


def test_stack_answer_files_are_ignored_where_stacks_overlay_project_roots() -> None:
    for path in (ROOT_GITIGNORE, PROJECT_GITIGNORE, LOCAL_STACK_GITIGNORE, DEV_STACK_GITIGNORE):
        assert "/.copier-answers.stack.yml" in path.read_text(encoding="utf-8")
    for stack in (_render_dev_stack(), _render_dev_docker_stack(), _render_local_stack()):
        assert stack["template"]["answers_file"] == ".copier-answers.stack.yml"


def test_dev_stack_local_processes_do_not_depend_on_container_services() -> None:
    stack = _render_dev_stack()

    container_services = {name for name, service in stack["services"].items() if service.get("runtime") == "container"}
    local_processes = stack.get("jobs", {}) | {
        name: service for name, service in stack["services"].items() if service.get("runtime") == "local"
    }

    for name, process in local_processes.items():
        dependencies = set(process.get("depends_on", [])) | set(process.get("after", []))
        assert not dependencies & container_services, name


def test_celery_queue_workers_render_in_both_modes() -> None:
    """`celery_queues` renders one dedicated `celery-<queue>` worker per entry.

    The queue worker inherits the celery block's env/command owner (no duplicated
    stack facts) and isolates long-lived addon tasks on a threads pool: `-Q <queue>`
    is the routing contract an addon like messaging_integrate_whatsapp dispatches to.
    A blank input (the default) renders no extra service.
    """

    for render in (_render_dev_stack, _render_dev_docker_stack, _render_local_stack):
        stack = render()
        assert {name for name in stack["services"] if name.startswith("celery-")} == {"celery-worker"}
        assert stack["services"]["celery-worker"]["stop_grace_period"] == "30s"

    dev = _render_dev_stack(celery_queues="whatsapp")
    dev_service = dev["services"]["celery-whatsapp"]
    assert dev_service["runtime"] == "local"
    assert dev_service["stop_grace_period"] == "30s"
    command = dev_service["command"]
    assert command[command.index("-Q") + 1] == "whatsapp"
    assert command[command.index("--pool") + 1] == "threads"
    # Queue workers never embed beat: one scheduler per stack.
    assert "--beat" not in command
    # The queue worker shares the shared block's env owner verbatim.
    assert dev_service["env"] == dev["services"]["celery-worker"]["env"]

    dev_docker = _render_dev_docker_stack(celery_queues="whatsapp")
    dev_docker_service = dev_docker["services"]["celery-whatsapp"]
    assert dev_docker_service["runtime"] == "container"
    assert "uv sync --inexact && exec celery" in dev_docker_service["command"][-1]
    assert "worker -Q whatsapp --pool threads --concurrency 8" in dev_docker_service["command"][-1]
    assert dev_docker_service["env"] == dev_docker["services"]["celery-worker"]["env"]

    local = _render_local_stack(celery_queues="whatsapp")
    local_service = local["services"]["celery-whatsapp"]
    assert local_service["runtime"] == "container"
    exec_line = local_service["command"][-1]
    assert "worker -Q whatsapp --pool threads --concurrency 8" in exec_line
    assert local_service["image"] == local["services"]["celery-worker"]["image"]

    # Two queues render two workers; the shared worker stays untouched.
    two = _render_dev_stack(celery_queues="whatsapp,voice")
    assert {"celery-whatsapp", "celery-voice", "celery-worker"} <= set(two["services"])


_BACKEND_DSN = "https://backend@sentry.example.invalid/1"
_WEB_DSN = "https://web@sentry.example.invalid/2"


def _env_nodes(stack: dict[str, Any], key: str) -> dict[str, dict[str, str]]:
    """Every service or job whose environment carries ``key``, by name."""

    return {
        name: node["env"]
        for section in ("services", "jobs")
        for name, node in stack[section].items()
        if key in (node.get("env") or {})
    }


@pytest.mark.parametrize(
    "stack",
    [
        pytest.param(lambda: _render_dev_stack(), id="dev-process"),
        pytest.param(lambda: _render_dev_stack(_runtime_mode="docker"), id="dev-docker"),
        pytest.param(lambda: _render_dev_stack(_runtime_mode="docker", serve_mode="production"), id="dev-production"),
        pytest.param(lambda: _render_local_stack(serve_mode="production"), id="local"),
    ],
)
def test_empty_sentry_dsns_render_no_error_reporting(stack: Any) -> None:
    rendered = json.dumps(stack())

    assert "SENTRY" not in rendered


@pytest.mark.parametrize(
    ("render", "environment"),
    [
        (lambda **inputs: _render_dev_stack(**inputs), "development"),
        (lambda **inputs: _render_dev_stack(_runtime_mode="docker", **inputs), "development"),
        (lambda **inputs: _render_dev_stack(_runtime_mode="docker", serve_mode="production", **inputs), "production"),
        (lambda **inputs: _render_local_stack(serve_mode="production", **inputs), "production"),
    ],
    ids=["dev-process", "dev-docker", "dev-production", "local"],
)
def test_sentry_dsns_reach_python_processes_and_the_spa(render: Any, environment: str) -> None:
    stack = render(sentry_dsn=_BACKEND_DSN, sentry_web_dsn=_WEB_DSN)

    python_nodes = _env_nodes(stack, "DATABASE_URL")
    assert python_nodes
    for name, env in python_nodes.items():
        assert (env["SENTRY_DSN"], env["SENTRY_ENVIRONMENT"]) == (_BACKEND_DSN, environment), name
        assert "VITE_SENTRY_DSN" not in env, name

    web_nodes = _env_nodes(stack, "VITE_SENTRY_DSN")
    assert web_nodes
    assert set(web_nodes).isdisjoint(python_nodes)
    for name, env in web_nodes.items():
        assert (env["VITE_SENTRY_DSN"], env["VITE_SENTRY_ENVIRONMENT"]) == (_WEB_DSN, environment), name
        assert "SENTRY_DSN" not in env, name


def test_each_sentry_dsn_is_independent() -> None:
    backend_only = _render_dev_stack(sentry_dsn=_BACKEND_DSN)
    web_only = _render_dev_stack(sentry_web_dsn=_WEB_DSN)

    assert _env_nodes(backend_only, "SENTRY_DSN") and not _env_nodes(backend_only, "VITE_SENTRY_DSN")
    assert _env_nodes(web_only, "VITE_SENTRY_DSN") and not _env_nodes(web_only, "SENTRY_DSN")
