"""Regression coverage for operator stack templates.

Both stack templates (``stacks/dev`` = process mode, ``stacks/local`` = docker mode)
render from ONE shared manifest body (``stacks/_shared/stack-body.yaml.jinja``): each
``angee.yaml.jinja`` is a thin ``{% set %}`` header that includes it. The mini-renderer
below inlines that include, then evaluates the template constructs the operator's
pongo2 engine handles — ``{% set %}``, ``{% if VAR == "VAL" %}`` blocks, the celery
``{% for role in [...] %}`` loop, and inline ``{% if flag %}`` / ``{% if VAR == "VAL" %}``
command guards — so the contract tests pin whatever the templates compute, never a
value re-derived here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROOT_GITIGNORE = ROOT / ".gitignore"
LOCAL_COPIER = ROOT / "templates" / "stacks" / "local" / "copier.yml"
LOCAL_TEMPLATE = ROOT / "templates" / "stacks" / "local" / "template" / "angee.yaml.jinja"
LOCAL_STACK_GITIGNORE = ROOT / "templates" / "stacks" / "local" / "template" / ".gitignore.jinja"
DEV_COPIER = ROOT / "templates" / "stacks" / "dev" / "copier.yml"
DEV_TEMPLATE = ROOT / "templates" / "stacks" / "dev" / "template" / "{{ ANGEE_ROOT }}" / "angee.yaml.jinja"
SHARED_BODY = ROOT / "templates" / "stacks" / "_shared" / "stack-body.yaml.jinja"
PROJECT_GITIGNORE = ROOT / "templates" / "projects" / "web" / "template" / ".gitignore.jinja"
PROJECT_SETTINGS_TEMPLATE = ROOT / "templates" / "projects" / "web" / "template" / "settings.yaml.jinja"

# Services both stack templates render from the one shared body.
SHARED_SERVICES = {"operator", "postgres", "redis", "django", "celery-worker", "celery-beat"}


# --- the mini-renderer ---------------------------------------------------------

_INCLUDE = re.compile(r'{%\s*include\s+"([^"]+)"\s*%}')
_JINJA_TAG = re.compile(r"{%\s*(.*?)\s*%}")
_BLOCK_IF = re.compile(r'^{%\s*if\s+(\w+)\s*==\s*"([^"]*)"\s*%}$')
_BLOCK_ELIF = re.compile(r'^{%\s*elif\s+(\w+)\s*==\s*"([^"]*)"\s*%}$')


def _render_stack_manifest(manifest_path: Path, variables: dict[str, str]) -> dict[str, Any]:
    """Render a wrapper manifest + its shared body into a YAML contract dict.

    Runs the template passes in dependency order: inline the shared-body include,
    strip comments, bind ``{% set %}`` variables, evaluate the ``{% if VAR == "VAL" %}``
    mode/framework blocks, expand the celery ``{% for %}`` loop, resolve the inline
    ``{% if VAR == "VAL" %}`` and ``{% if flag %}`` command guards, then substitute the
    remaining ``{{ var }}`` interpolations.
    """

    text = _inline_includes(manifest_path)
    text = _strip_jinja_comments(text)
    text = _render_jinja_set_tags(text, variables)
    text = _render_conditional_blocks(text, variables)
    text = _render_for_loops(text)
    text = _render_inline_eq_conditionals(text, variables)
    text = _render_inline_flag_conditionals(text, variables)
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


def _render_conditional_blocks(text: str, variables: dict[str, str]) -> str:
    """Evaluate standalone `{% if VAR == "VAL" %}` / elif / else / endif blocks.

    A frame stack keeps parent activity, so nested guards — e.g. the docker
    ``framework == "source"`` source inside the ``runtime_mode == "docker"`` block —
    resolve correctly. Only whole-line tags are treated as block boundaries; an inline
    ``{% if … %}…{% endif %}`` on a content line (with trailing text) passes through
    to the inline passes.
    """

    frames: list[dict[str, bool]] = []
    output: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        match_if = _BLOCK_IF.match(stripped)
        if match_if:
            parent = _parent_active(frames)
            active = parent and variables.get(match_if.group(1)) == match_if.group(2)
            frames.append({"active": active, "matched": active, "parent": parent})
            continue
        match_elif = _BLOCK_ELIF.match(stripped)
        if match_elif:
            frame = frames[-1]
            active = (
                frame["parent"]
                and not frame["matched"]
                and variables.get(match_elif.group(1)) == match_elif.group(2)
            )
            frame["active"] = active
            frame["matched"] = frame["matched"] or active
            continue
        if stripped == "{% else %}":
            frame = frames[-1]
            active = frame["parent"] and not frame["matched"]
            frame["active"] = active
            frame["matched"] = True
            continue
        if stripped == "{% endif %}":
            frames.pop()
            continue
        if _parent_active(frames):
            output.append(line)

    assert not frames
    return "\n".join(output) + "\n"


def _parent_active(frames: list[dict[str, bool]]) -> bool:
    return all(frame["active"] for frame in frames)


def _render_for_loops(text: str) -> str:
    """Expand the celery `{% for role in "worker,beat"|split:"," %}…{% endfor %}` loop.

    pongo2 has no list literals in expressions and takes Django-style (colon)
    filter args, so the template iterates a split string — this renderer expands
    exactly that form.
    """

    def expand(match: re.Match[str]) -> str:
        items = [item.strip() for item in match.group(1).split(match.group(2))]
        body = match.group(3)
        return "".join(body.replace("{{ role }}", item) for item in items)

    return re.sub(
        r'{%\s*for\s+role\s+in\s+"([^"]*)"\|split:"([^"]*)"\s*%}(.*?){%\s*endfor\s*%}',
        expand,
        text,
        flags=re.DOTALL,
    )


def _render_inline_eq_conditionals(text: str, variables: dict[str, str]) -> str:
    """Resolve inline `{% if VAR == "VAL" %}…{% endif %}` command guards (same line)."""

    return re.sub(
        r'{%\s*if\s+(\w+)\s*==\s*"([^"]*)"\s*%}(.*?){%\s*endif\s*%}',
        lambda m: m.group(3) if variables.get(m.group(1)) == m.group(2) else "",
        text,
    )


def _render_inline_flag_conditionals(text: str, variables: dict[str, str]) -> str:
    """Resolve inline `{% if <flag> %}…{% endif %}` command guards by truthiness."""

    return re.sub(
        r"{%\s*if\s+(\w+)\s*%}(.*?){%\s*endif\s*%}",
        lambda m: m.group(2) if variables.get(m.group(1)) else "",
        text,
    )


def _eval_condition(condition: str, variables: dict[str, str]) -> bool:
    left, _, right = condition.partition("==")
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


def _render_local_stack(*, framework: str = "source") -> dict[str, Any]:
    """Render the docker-mode local stack enough for YAML contract tests."""

    variables = {
        "_src_path": "https://github.com/ang-ee/angee-django/tree/v0.1.7/templates/stacks/local",
        "caddy_image": "caddy:2.9-alpine",
        "django_image": "ghcr.io/ang-ee/django-angee-base:latest",
        "django_port": "8000",
        "framework": framework,
        "instance_name": "angee-local",
        "operator_port": "9000",
        "ui_port": "5173",
        "web_image": "ghcr.io/ang-ee/angee-web:latest",
        "web_path": "web",
    }
    return _render_stack_manifest(LOCAL_TEMPLATE, variables)


def _render_dev_stack(
    *,
    project_path: str = "../examples/notes-angee",
    framework_path: str = "..",
) -> dict[str, Any]:
    """Render the process-mode dev stack enough for YAML contract tests.

    ``project_path`` / ``framework_path`` model what the TEMPLATE receives: the
    operator (copierx.ResolvePathInputs) rewrites relative ``type: path`` inputs to
    be ANGEE_ROOT-relative in every render flow before the template runs (logical
    "examples/notes-angee" arrives as "../examples/notes-angee"; repo-layout "."
    arrives as ".."), and passes absolute inputs through verbatim. The wrapper uses
    them AS-IS; only the ``uv_project`` guard keys off the rewritten value.
    """

    variables = {
        "ANGEE_ROOT": ".angee",
        "django_port": "8100",
        "edge_port": "7001",
        "framework_path": framework_path,
        "operator_port": "9000",
        "postgres_port": "5433",
        "process_compose_port": "10000",
        "project_name": "notes-angee-dev",
        "project_path": project_path,
        "redis_port": "6379",
        "storybook_port": "6006",
        "ui_port": "5173",
        "web_path": "web",
    }
    return _render_stack_manifest(DEV_TEMPLATE, variables)


def _render_project_settings(*, addon_installer_backend: str, include_operator_installer: bool) -> dict[str, Any]:
    """Render project settings enough for stack-owned installer contract tests."""

    text = PROJECT_SETTINGS_TEMPLATE.read_text(encoding="utf-8")
    text = _render_project_settings_conditionals(
        text,
        addon_installer_backend=addon_installer_backend,
        include_operator_installer=include_operator_installer,
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


def _render_project_settings_conditionals(
    text: str,
    *,
    addon_installer_backend: str,
    include_operator_installer: bool,
) -> str:
    """Evaluate the simple settings-template conditionals these tests need."""

    frames: list[bool] = []
    output: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "{% if include_operator_installer %}":
            frames.append(include_operator_installer and all(frames))
            continue
        if stripped == '{% if addon_installer_backend != "local" %}':
            frames.append((addon_installer_backend != "local") and all(frames))
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
    local = _render_local_stack()
    assert SHARED_SERVICES <= set(dev["services"])
    assert SHARED_SERVICES <= set(local["services"])


# --- local (docker) contracts --------------------------------------------------


def test_local_stack_copier_contract() -> None:
    manifest = yaml.safe_load(LOCAL_COPIER.read_text(encoding="utf-8"))

    assert "angee dev" in manifest["_message_after_copy"]
    assert "ANGEE_SECRET_OPERATOR_TOKEN" in manifest["_message_after_copy"]
    # frontend_mode / base_image are gone; framework + django_image replace them.
    assert "frontend_mode" not in manifest
    assert "base_image" not in manifest
    assert manifest["framework"]["default"] == "source"
    assert manifest["framework"]["choices"] == ["source", "baked"]
    assert manifest["django_image"]["default"] == "ghcr.io/ang-ee/django-angee-base:latest"
    assert manifest["caddy_image"]["default"] == "caddy:2.9-alpine"


def test_local_django_source_mode_links_framework_editable_on_base_image() -> None:
    """Default source mode runs the deps-only base image and links the checkout at start."""

    stack = _render_local_stack(framework="source")
    django = stack["services"]["django"]

    assert django["image"] == "ghcr.io/ang-ee/django-angee-base:latest"
    command = django["command"][-1]
    assert "uv sync --frozen --inexact --extra postgres --project sources/angee-django" in command
    assert "python manage.py angee provision --bootstrap-admin" in command
    assert "exec python -m uvicorn angee.asgi:application --host 0.0.0.0 --port 8000" in command
    # The PYTHONPATH hack is deleted — the editable link owns the framework on sys.path.
    assert "PYTHONPATH" not in django["env"]

    assert stack["sources"]["framework"]["path"] == "sources/angee-django"
    for service_name in ("celery-worker", "celery-beat"):
        service = stack["services"][service_name]
        assert service["image"] == "ghcr.io/ang-ee/django-angee-base:latest"
        assert "PYTHONPATH" not in service["env"]
        assert "uv sync --frozen --inexact --extra postgres --project sources/angee-django" in service["command"][-1]


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
    assert "celery -A angee.tasks.celery:app worker" in stack["services"]["celery-worker"]["command"][-1]
    assert "celery -A angee.tasks.celery:app beat" in stack["services"]["celery-beat"]["command"][-1]

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
    # source-mode graft: overlay each @angee package's src/ from the framework checkout,
    # then symlink each package into the mounted project's node_modules.
    assert "project/sources/angee-django/angee/web" in frontend_command
    assert "project/sources/angee-django/addons/angee" in frontend_command
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
    assert stack["services"]["operator"]["env"]["ANGEE_OPERATOR_TOKEN"] == "${secret.operator-token}"
    assert '--token "$ANGEE_OPERATOR_TOKEN"' in stack["services"]["operator"]["command"][-1]


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


# --- dev (process) contracts ---------------------------------------------------


def test_dev_stack_has_exactly_the_four_lifecycle_jobs() -> None:
    """The eight-job DAG collapses to deps + provision + operator-schema + codegen."""

    stack = _render_dev_stack()

    assert set(stack["jobs"]) == {"deps", "provision", "operator-schema", "codegen"}
    provision = " ".join(stack["jobs"]["provision"]["command"])
    assert "manage.py angee provision --demo --force-rebac" in provision
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


def test_dev_stack_mounts_postgres_data_from_generated_stack_dir() -> None:
    stack = _render_dev_stack()

    assert stack["persist"]["pgdata"]["subpath"] == ".angee/pgdata"
    assert stack["services"]["postgres"]["mounts"] == ["bind://./pgdata:/var/lib/postgresql/data"]
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


def test_dev_stack_keeps_the_process_only_frontend_services() -> None:
    stack = _render_dev_stack()

    assert "process_compose" in stack["ports"]
    assert "frontend" in stack["services"]
    assert "storybook" in stack["services"]
    assert "caddy" not in stack["services"]
    assert stack["services"]["frontend"]["command"] == ["pnpm", "--dir", "web", "dev"]
    assert "provision" in stack["services"]["frontend"]["after"]


def test_dev_stack_source_paths_pass_through_the_operator_rewritten_inputs() -> None:
    """The wrapper uses the operator-rewritten inputs AS-IS for the repo-level layout.

    The operator rewrites the logical defaults (project "examples/notes-angee",
    framework ".") to ANGEE_ROOT-relative values before the template renders; the
    framework is an ancestor of the project root, so ``uv run`` discovers its
    pyproject by walking up — no ``--project``. Re-translating in the template
    (the old ``../``+input math) double-counted the hop in every render flow.
    """

    stack = _render_dev_stack()  # operator-rewritten defaults

    assert stack["sources"]["app"]["path"] == "../examples/notes-angee"
    assert stack["sources"]["framework"]["path"] == ".."
    provision = stack["jobs"]["provision"]
    assert provision["workdir"] == "source://app"
    assert "--project" not in provision["command"]
    assert "uv run --extra postgres" in " ".join(provision["command"])


def test_dev_stack_external_framework_checkout_is_absolute_and_drives_uv_project() -> None:
    """A per-project `.angee/` with the framework as a separate checkout.

    The framework is not an ancestor of the project root, so every ``uv run``
    gains ``--project <framework>``. uv resolves --project against the job's
    workdir (the project root), so an external checkout must be given ABSOLUTE —
    an ANGEE_ROOT-relative value would point at the wrong directory.
    """

    stack = _render_dev_stack(project_path="..", framework_path="/opt/checkouts/angee-django")

    assert stack["sources"]["app"]["path"] == ".."
    assert stack["sources"]["framework"]["path"] == "/opt/checkouts/angee-django"
    provision = stack["jobs"]["provision"]
    assert provision["workdir"] == "source://app"
    assert provision["command"][:4] == ["uv", "run", "--project", "/opt/checkouts/angee-django"]


def test_dev_stack_keeps_absolute_source_paths_verbatim() -> None:
    """Absolute copier inputs are kept as-is (neither `../`-prefixed nor collapsed)."""

    stack = _render_dev_stack(project_path="/srv/project", framework_path="/opt/angee-django")

    assert stack["sources"]["app"]["path"] == "/srv/project"
    assert stack["sources"]["framework"]["path"] == "/opt/angee-django"
    assert stack["jobs"]["provision"]["command"][:4] == ["uv", "run", "--project", "/opt/angee-django"]


def test_dev_stack_keeps_stack_answers_separate_from_workspace_answers() -> None:
    manifest = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))
    stack = _render_dev_stack()

    assert manifest["_answers_file"] == ".copier-answers.stack.yml"
    assert stack["template"]["answers_file"] == ".copier-answers.stack.yml"


def test_dev_stack_prunes_dead_playwright_inputs() -> None:
    manifest = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))

    assert "playwright_port" not in manifest
    assert "playwright_browser" not in manifest
    assert "process_compose_port" in manifest


def test_stack_answer_files_are_ignored_where_stacks_overlay_project_roots() -> None:
    for path in (ROOT_GITIGNORE, PROJECT_GITIGNORE, LOCAL_STACK_GITIGNORE):
        assert "/.copier-answers.stack.yml" in path.read_text(encoding="utf-8")


def test_dev_stack_local_processes_do_not_depend_on_container_services() -> None:
    stack = _render_dev_stack()

    container_services = {name for name, service in stack["services"].items() if service.get("runtime") == "container"}
    local_processes = stack.get("jobs", {}) | {
        name: service for name, service in stack["services"].items() if service.get("runtime") == "local"
    }

    for name, process in local_processes.items():
        dependencies = set(process.get("depends_on", [])) | set(process.get("after", []))
        assert not dependencies & container_services, name
