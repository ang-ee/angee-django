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

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROOT_GITIGNORE = ROOT / ".gitignore"
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
DEV_TEMPLATES_SYMLINK = DEV_TEMPLATE.with_name("templates")
SHARED_BODY = ROOT / "templates" / "stacks" / "_shared" / "stack-body.yaml.jinja"
SHARED_AGENTS = ROOT / "templates" / "stacks" / "_shared" / "AGENTS.md.jinja"
PROJECT_GITIGNORE = ROOT / "templates" / "projects" / "web" / "template" / ".gitignore.jinja"
PROJECT_PYPROJECT_TEMPLATE = ROOT / "templates" / "projects" / "web" / "template" / "pyproject.toml.jinja"
PROJECT_SETTINGS_TEMPLATE = ROOT / "templates" / "projects" / "web" / "template" / "settings.yaml.jinja"

# Services both stack templates render from the one shared body.
SHARED_SERVICES = {"operator", "postgres", "redis", "django", "celery-worker", "celery-beat"}


# --- the mini-renderer ---------------------------------------------------------

_INCLUDE = re.compile(r'{%\s*include\s+"([^"]+)"\s*%}')
_JINJA_TAG = re.compile(r"{%\s*(.*?)\s*%}")
_CONDITIONAL_TAG = re.compile(r"{%\s*(if\s+.*?|elif\s+.*?|else|endif)\s*%}")


def _render_stack_manifest(manifest_path: Path, variables: dict[str, str]) -> dict[str, Any]:
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

    text = manifest_path.read_text(encoding="utf-8")
    base = _template_subdirectory(manifest_path)

    def repl(match: re.Match[str]) -> str:
        included = (base / match.group(1)).resolve()
        return included.read_text(encoding="utf-8")

    return _INCLUDE.sub(repl, text)


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

    active: bool | None = None  # None ⇒ unconditional (a bare `{% set %}` line)
    branch_taken = False
    for body in _JINJA_TAG.findall(line):
        if body.startswith("if "):
            active = _eval_condition(body[len("if ") :], variables)
            branch_taken = active
        elif body.startswith("elif "):
            active = not branch_taken and _eval_condition(body[len("elif ") :], variables)
            branch_taken = branch_taken or active
        elif body == "else":
            active = not branch_taken
            branch_taken = True
        elif body == "endif":
            active = None
        elif body.startswith("set ") and active is not False:
            name, _, expr = body[len("set ") :].partition("=")
            variables[name.strip()] = _eval_expr(expr, variables)


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
            active = (
                frame["parent"]
                and not frame["matched"]
                and _eval_condition(body[len("elif ") :], variables)
            )
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
    # pongo2 `and`: every conjunct must be truthy. Split before the == check so a
    # conjunct can itself be an equality test.
    if " and " in condition:
        return all(_eval_condition(part, variables) for part in condition.split(" and "))
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
    return "".join(_eval_atom(atom, variables) for atom in expr.split("+"))


def _eval_atom(atom: str, variables: dict[str, str]) -> str:
    atom = atom.strip()
    if atom.startswith('"') and atom.endswith('"'):
        return atom[1:-1]
    return variables[atom]


# --- per-template renderers ----------------------------------------------------


def _render_local_stack(*, framework: str = "source", celery_queues: str = "") -> dict[str, Any]:
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
    celery_queues: str = "",
    enable_ollama: bool = False,
    ollama_port: str = "11434",
    ingress_domain: str = "localhost",
    _runtime_mode: str = "process",
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
        "postgres_port": "5433",
        "process_compose_port": "8080",
        "project_name": "app",
        "project_path": project_path,
        "redis_port": "6379",
        "runtime_mode": _runtime_mode,
        "storybook_port": "6006",
        "ui_port": "5173",
        "web_path": "web",
        "work_state_source": work_state_source,
        "work_state_repo": work_state_repo,
        "work_state_ref": work_state_ref,
    }
    return _render_stack_manifest(DEV_TEMPLATE, variables)


def _render_dev_docker_stack(
    *, celery_queues: str = "", ingress_domain: str = "localhost"
) -> dict[str, Any]:
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
    ):
        assert contract in instructions

    include = '{% include "../../_shared/AGENTS.md.jinja" %}'
    for agents_template in (DEV_AGENTS_TEMPLATE, LOCAL_AGENTS_TEMPLATE):
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


def test_local_django_source_mode_bootstraps_fresh_host_dependencies() -> None:
    """Source containers project and install addon deps before importing Django apps."""

    stack = _render_local_stack(framework="source")
    django = stack["services"]["django"]

    assert django["image"] == "ghcr.io/ang-ee/django-angee-base:latest"
    command = django["command"][-1]
    assert "uv sync --frozen --inexact --extra postgres --project sources/angee" in command
    assert "python -m angee.compose.bootstrap" in command
    assert "uv sync --inexact" in command
    assert "python manage.py angee provision --bootstrap-admin" in command
    assert command.index("python -m angee.compose.bootstrap") < command.index("uv sync --inexact")
    assert command.index("uv sync --inexact") < command.index("python manage.py angee provision")
    assert "exec python -m uvicorn angee.asgi:application --host 0.0.0.0 --port 8000" in command
    # The PYTHONPATH hack is deleted — the editable link owns the framework on sys.path.
    assert "PYTHONPATH" not in django["env"]

    assert stack["sources"]["framework"]["path"] == "sources/angee"
    for service_name in ("celery-worker", "celery-beat"):
        service = stack["services"][service_name]
        assert service["image"] == "ghcr.io/ang-ee/django-angee-base:latest"
        assert "PYTHONPATH" not in service["env"]
        assert "uv sync --frozen --inexact --extra postgres --project sources/angee" in service["command"][-1]
        assert service["command"][-1].index("done;") < service["command"][-1].index("uv sync --inexact")
        assert service["command"][-1].index("uv sync --inexact") < service["command"][-1].index("exec celery")


def test_local_django_baked_mode_skips_uv_sync() -> None:
    """Baked mode runs a code-baked image, so it never links a source checkout."""

    stack = _render_local_stack(framework="baked")
    django = stack["services"]["django"]

    assert "uv sync" not in django["command"][-1]
    assert "python manage.py angee provision --bootstrap-admin" in django["command"][-1]
    assert "framework" not in stack["sources"]
    for service_name in ("celery-worker", "celery-beat"):
        assert "uv sync" not in stack["services"][service_name]["command"][-1]


def test_local_stack_renders_single_caddy_frontend_ingress() -> None:
    stack = _render_local_stack()

    assert "vite" not in stack["services"]
    assert "jobs" not in stack
    assert "frontend-build" in stack["services"]
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
    assert "celery -A angee.jobs.celery:app beat" in stack["services"]["celery-beat"]["command"][-1]

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

    frontend_command = stack["services"]["frontend-build"]["command"][-1]
    # Source-mode graft: overlay each @angee package src from the monorepo and
    # external bridge checkout, then symlink it into the mounted project.
    assert "project/sources/angee/packages" in frontend_command
    assert "project/sources/angee/addons/angee" in frontend_command
    assert "project/sources/angee-messaging-bridges/addons/angee" in frontend_command
    assert "fs.cpSync(srcDir,dstDir" in frontend_command
    assert 'path.join(root,"project/web/node_modules/@angee")' in frontend_command
    assert "fs.symlinkSync" in frontend_command
    assert "pnpm build" in frontend_command
    assert "exec tail -f /dev/null" in frontend_command


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


def test_project_template_defaults_to_local_addon_installer() -> None:
    """Plain generated projects keep the dev/local writer unless a stack opts in."""

    settings = _render_project_settings(addon_installer_backend="local", include_operator_installer=False)

    assert "angee.platform_integrate_operator" not in settings["INSTALLED_APPS"]
    assert "ANGEE_ADDON_INSTALLER_BACKEND" not in settings


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
    # The Matrix bridge (and its python-olm sdist chain) is opt-in, so no
    # build-variables block renders for the default full roster either.
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


def test_dev_stack_has_exactly_the_four_lifecycle_jobs() -> None:
    """The eight-job DAG collapses to deps + provision + operator-schema + codegen."""

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
    # provision has no depends_on — it owns waiting for the DB itself.
    assert "depends_on" not in stack["jobs"]["provision"]
    assert stack["jobs"]["operator-schema"]["depends_on"] == ["operator", "provision"]
    assert stack["jobs"]["codegen"]["depends_on"] == ["deps", "provision", "operator-schema"]
    # The serving processes now hang off provision, not the old resources/schema jobs.
    assert stack["services"]["django"]["after"] == ["provision"]
    assert stack["services"]["celery-worker"]["after"] == ["provision"]
    assert stack["services"]["celery-beat"]["after"] == ["provision"]


def test_dev_stack_mounts_postgres_data_from_stack_root() -> None:
    stack = _render_dev_stack()

    assert stack["persist"]["pgdata"]["subpath"] == "./data/pgdata"
    assert stack["persist"]["app-data"]["subpath"] == "./data"
    assert stack["services"]["postgres"]["mounts"] == ["bind://./data/pgdata:/var/lib/postgresql/data"]
    assert stack["services"]["postgres"]["ports"] == ["${ports.postgres}:5432"]


def test_dev_stack_runs_redis_and_celery_services() -> None:
    stack = _render_dev_stack()

    assert "redis" in stack["services"]
    assert stack["services"]["redis"]["ports"] == ["${ports.redis}:6379"]
    assert stack["services"]["django"]["env"]["REDIS_URL"] == "redis://127.0.0.1:${ports.redis}/0"
    assert stack["services"]["django"]["env"]["CELERY_BROKER_URL"] == "redis://127.0.0.1:${ports.redis}/1"
    assert stack["services"]["celery-worker"]["env"]["CELERY_BROKER_URL"] == "redis://127.0.0.1:${ports.redis}/1"
    assert "celery" in stack["services"]["celery-worker"]["command"]
    assert "worker" in stack["services"]["celery-worker"]["command"]
    assert "celery" in stack["services"]["celery-beat"]["command"]
    assert "beat" in stack["services"]["celery-beat"]["command"]


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
    assert "provision" in stack["services"]["frontend"]["after"]

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
        stack["services"]["celery-beat"],
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
    assert "work-angee" not in name_only["sources"]

    # Repo only (no name): nothing to key the source on, so no work-state wiring.
    repo_only = _render_dev_stack(work_state_repo="git@github.com:ang-ee/work-angee.git")
    assert "inputs" not in repo_only["workspaces"]["src"]
    assert set(repo_only["sources"]) == {"app", "framework", "angee"}

    # The local docker instance keeps its own source story (framework checkout at
    # sources/angee) — no framework git-source block, no workspace cut.
    local = _render_local_stack()
    assert set(local["sources"]) == {"app", "framework"}
    assert "workspaces" not in local


def test_dev_stack_docker_mode_is_containerized_framework_dev() -> None:
    """Docker mode keeps the framework roster while folding jobs into containers."""

    stack = _render_dev_docker_stack()

    assert set(stack["sources"]) == {"app", "framework", "angee"}
    assert stack["sources"]["angee"]["kind"] == "git"
    assert stack["sources"]["framework"]["path"] == "workspaces/src/angee"
    assert stack["workspaces"]["src"] == {"template": "workspaces/src"}

    assert "jobs" not in stack
    operator_svc = stack["services"]["operator"]
    assert operator_svc["runtime"] == "container"
    assert operator_svc["image"] == "ghcr.io/ang-ee/angee-operator:latest"
    assert operator_svc["ports"] == ["${ports.operator}:9000"]
    assert "bind://.:${stack.root}" in operator_svc["mounts"]
    assert stack["services"]["django"]["env"]["ANGEE_OPERATOR_URL"] == "http://operator:9000"
    for name in ("django", "celery-worker", "celery-beat", "frontend", "storybook"):
        assert stack["services"][name]["runtime"] == "container"

    django = stack["services"]["django"]
    django_command = django["command"][-1]
    for fragment in (
        "python -m angee.compose.bootstrap",
        "python manage.py angee provision --demo --force-rebac",
        "python manage.py operator_schema",
        "python manage.py runserver 0.0.0.0:8000",
    ):
        assert fragment in django_command
    assert django["env"]["DATABASE_URL"] == "postgres://angee:${secret.db-password}@postgres:5432/angee"
    assert django["env"]["ANGEE_OPERATOR_URL"] == "http://operator:9000"
    for name in ("celery-worker", "celery-beat"):
        celery_command = stack["services"][name]["command"][-1]
        # Pre-wait sync gives the wait probe psycopg; the post-wait sync installs the
        # addon group django's bootstrap projected (axes, …) before celery composes.
        assert "uv sync --inexact; n=0" in celery_command
        assert celery_command.index("done;") < celery_command.rindex("uv sync --inexact")
        assert celery_command.rindex("uv sync --inexact") < celery_command.index("exec celery")

    frontend = stack["services"]["frontend"]
    assert frontend["image"] == "node:22-bookworm-slim"
    assert frontend["mounts"] == ["source://app:/app"]
    assert frontend["env"]["ANGEE_DJANGO_URL"] == "http://django:8000"
    frontend_command = frontend["command"][-1]
    assert frontend_command.startswith("rm -f caches/js-deps.done && corepack enable pnpm && pnpm install")
    assert "runtime/schemas/console.graphql" in frontend_command
    assert "runtime/schemas/public.graphql" in frontend_command
    assert "pnpm --dir web codegen" in frontend_command
    assert "date > caches/js-deps.done" in frontend_command
    assert "exec pnpm --dir web dev --host 0.0.0.0" in frontend_command
    assert frontend["ports"] == ["${ports.ui}:5173"]
    assert "ANGEE_UI_ALLOWED_HOSTS" not in frontend["env"]

    storybook = stack["services"]["storybook"]
    assert storybook["image"] == "node:22-bookworm-slim"
    # Each container enables its own corepack shim — the frontend container's
    # `corepack enable` does not reach sibling containers.
    assert storybook["command"][-1].startswith("corepack enable pnpm;")
    assert "until [ -s caches/js-deps.done ]" in storybook["command"][-1]
    assert "exec pnpm --filter @angee/storybook dev --no-open --host 0.0.0.0" in storybook["command"][-1]
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
        "  - \"workspaces/src/angee/packages/*\"",
        "  - \"workspaces/src/angee/addons/angee/*/web\"",
    )
    full_profile_globs = (
        "  - \"workspaces/src/angee/examples/addons/*/*/web\"",
        "  - \"workspaces/src/angee/examples/e2e\"",
    )
    guard_index = workspace.index("{% if addons_profile == \"full\" %}")
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
    # the consolidated angee source cache.
    assert DEV_TEMPLATES_SYMLINK.is_symlink()
    assert str(DEV_TEMPLATES_SYMLINK.readlink()) == "sources/angee/templates"


def test_uv_caches_are_stack_owned() -> None:
    """Every stack pins uv's cache inside the stack.

    The dev stack needs no override — the rendered project pyproject pins
    ``cache-dir = "caches/uv"`` which uv resolves against the job CWD (the stack
    root). The docker instance overrides UV_CACHE_DIR to the container path of
    the same stack-owned dir.
    """

    dev = _render_dev_stack()
    for name in ("django", "celery-worker", "celery-beat"):
        assert "UV_CACHE_DIR" not in dev["services"][name]["env"]

    dev_docker = _render_dev_docker_stack()
    local = _render_local_stack()
    for stack in (dev_docker, local):
        for name in ("django", "celery-worker", "celery-beat"):
            assert stack["services"][name]["env"]["UV_CACHE_DIR"] == "/app/caches/uv"

    for gitignore_path in (LOCAL_STACK_GITIGNORE, DEV_STACK_GITIGNORE):
        assert "/caches/" in gitignore_path.read_text(encoding="utf-8")


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
        for name in ("django", "celery-worker", "celery-beat"):
            assert stack["services"][name]["env"]["YAMLCONF_SECRET_KEY"] == "${secret.secret-key}"
    assert dev["jobs"]["provision"]["env"]["YAMLCONF_SECRET_KEY"] == "${secret.secret-key}"


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
        assert {name for name in stack["services"] if name.startswith("celery-")} == {
            "celery-worker",
            "celery-beat",
        }

    dev = _render_dev_stack(celery_queues="whatsapp")
    dev_service = dev["services"]["celery-whatsapp"]
    assert dev_service["runtime"] == "local"
    command = dev_service["command"]
    assert command[command.index("-Q") + 1] == "whatsapp"
    assert command[command.index("--pool") + 1] == "threads"
    assert "beat" not in command
    # The queue worker shares the shared block's env owner verbatim.
    assert dev_service["env"] == dev["services"]["celery-worker"]["env"]

    dev_docker = _render_dev_docker_stack(celery_queues="whatsapp")
    dev_docker_service = dev_docker["services"]["celery-whatsapp"]
    assert dev_docker_service["runtime"] == "container"
    assert "uv sync --inexact; n=0" in dev_docker_service["command"][-1]
    assert "worker -Q whatsapp --pool threads --concurrency 8" in dev_docker_service["command"][-1]
    assert dev_docker_service["env"] == dev_docker["services"]["celery-worker"]["env"]

    local = _render_local_stack(celery_queues="whatsapp")
    local_service = local["services"]["celery-whatsapp"]
    assert local_service["runtime"] == "container"
    exec_line = local_service["command"][-1]
    assert "worker -Q whatsapp --pool threads --concurrency 8" in exec_line
    assert local_service["image"] == local["services"]["celery-worker"]["image"]

    # Two queues render two workers; the shared pair stays untouched.
    two = _render_dev_stack(celery_queues="whatsapp,voice")
    assert {"celery-whatsapp", "celery-voice", "celery-worker", "celery-beat"} <= set(two["services"])
