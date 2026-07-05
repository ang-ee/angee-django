"""Regression coverage for operator stack templates."""

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
PROJECT_GITIGNORE = ROOT / "templates" / "projects" / "web" / "template" / ".gitignore.jinja"
PROJECT_SETTINGS_TEMPLATE = ROOT / "templates" / "projects" / "web" / "template" / "settings.yaml.jinja"


def _render_local_stack(*, frontend_mode: str) -> dict[str, Any]:
    """Render the local stack template enough for YAML contract tests."""

    text = _render_frontend_mode_branches(LOCAL_TEMPLATE.read_text(encoding="utf-8"), frontend_mode)
    replacements = {
        "_src_path": "https://github.com/ang-ee/angee-django/tree/v0.1.7/templates/stacks/local",
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


def _render_dev_stack(
    *,
    project_path: str = "examples/notes-angee",
    framework_path: str = ".",
) -> dict[str, Any]:
    """Render the dev stack template enough for YAML contract tests.

    ``project_path`` / ``framework_path`` are the *logical* copier inputs (the
    `copier.yml` defaults render the repo-level layout). The template owns the
    stack-relative source-path translation and the per-job ``--project`` guard;
    this renderer evaluates the template's own ``{% set %}`` conditionals and
    inline ``{% if uv_project %}`` blocks so the contract tests pin whatever the
    template computes for each layout — never a value re-derived here.
    """

    variables: dict[str, str] = {
        "ANGEE_ROOT": ".angee",
        "django_port": "8100",
        "edge_port": "7001",
        "framework_path": framework_path,
        "operator_port": "9000",
        "postgres_port": "5433",
        "process_compose_port": "10000",
        "project_name": "notes-angee-dev",
        "project_path": project_path,
        "storybook_port": "6006",
        "ui_port": "5173",
        "web_path": "web",
    }
    text = _strip_jinja_comments(DEV_TEMPLATE.read_text(encoding="utf-8"))
    text = _render_jinja_set_tags(text, variables)
    text = _render_inline_flag_conditionals(text, variables)
    for key, value in variables.items():
        text = text.replace(f"{{{{ {key} }}}}", value)
    assert "{{" not in text
    assert "{%" not in text
    rendered = yaml.safe_load(text)
    assert isinstance(rendered, dict)
    return rendered


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
            frames.append(include_operator_installer and _project_parent_active(frames))
            continue
        if stripped == '{% if addon_installer_backend != "local" %}':
            frames.append((addon_installer_backend != "local") and _project_parent_active(frames))
            continue
        if stripped == "{% endif %}":
            frames.pop()
            continue
        if _project_parent_active(frames):
            output.append(line)

    assert not frames
    return "\n".join(output) + "\n"


def _project_parent_active(frames: list[bool]) -> bool:
    return all(frames)


_JINJA_TAG = re.compile(r"{%\s*(.*?)\s*%}")


def _strip_jinja_comments(text: str) -> str:
    """Drop `{# … #}` comment blocks the way Jinja does before rendering."""

    return re.sub(r"{#.*?#}", "", text, flags=re.DOTALL)


def _render_jinja_set_tags(text: str, variables: dict[str, str]) -> str:
    """Evaluate the dev template's `{% set %}` lines, binding into ``variables``.

    Handles both the plain ``{% set x = "v" %}`` line and the single-line
    ``{% if … %}{% set x = … %}{% elif … %}…{% else %}…{% endif %}`` source-path
    conditionals, so the derived source paths and the ``uv_project`` flag come
    straight from the template's own expressions.
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

    assert "angee dev" in manifest["_message_after_copy"]
    assert "ANGEE_SECRET_OPERATOR_TOKEN" in manifest["_message_after_copy"]
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
    assert stack["template"]["active"].endswith("/templates/stacks/local")
    assert stack["template"]["active"] != "stacks/local"
    assert "ports" not in stack["services"]["django"]
    assert stack["services"]["django"]["env"]["ANGEE_BUILTIN_MCP_URL"] == "http://django:8000/mcp"
    assert stack["persist"]["pgdata"]["subpath"] == "./data/pgdata"
    assert stack["services"]["postgres"]["mounts"] == ["bind://./data/pgdata:/var/lib/postgresql/data"]

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
    assert 'path.join(root,"project/web/node_modules/@angee")' in frontend_command
    assert "fs.symlinkSync" in frontend_command


def test_local_stack_vite_mode_preserves_legacy_direct_ports() -> None:
    stack = _render_local_stack(frontend_mode="vite")

    assert "jobs" not in stack
    assert "caddy" not in stack["services"]
    assert "vite" in stack["services"]
    assert stack["services"]["django"]["ports"] == ["8000:8000"]
    assert stack["services"]["vite"]["ports"] == ["5173:5173"]
    assert stack["services"]["vite"]["env"]["ANGEE_DJANGO_URL"] == "http://django:8000"
    assert 'path.join(root,"project/web/node_modules/@angee")' in stack["services"]["vite"]["command"][-1]
    assert "fs.symlinkSync" in stack["services"]["vite"]["command"][-1]


def test_local_stack_uses_operator_backed_addon_installer() -> None:
    """Containerized local stacks edit project files through the host operator."""

    manifest = yaml.safe_load(LOCAL_COPIER.read_text(encoding="utf-8"))
    chain_inputs = manifest["_angee"]["chain"][0]["inputs"]
    stack = _render_local_stack(frontend_mode="vite")

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


def test_dev_stack_mounts_postgres_data_from_generated_stack_dir() -> None:
    stack = _render_dev_stack()

    assert stack["persist"]["pgdata"]["subpath"] == ".angee/pgdata"
    assert stack["services"]["postgres"]["mounts"] == ["bind://./pgdata:/var/lib/postgresql/data"]


def test_dev_stack_source_paths_translate_for_the_repo_level_layout() -> None:
    """Default copier inputs (framework at the stack root) render the repo-level layout.

    The framework is an ancestor of the project root, so ``uv run`` discovers its
    pyproject by walking up — no ``--project`` — and the stack-relative inputs
    translate to project-root-relative source paths.
    """

    stack = _render_dev_stack()  # copier.yml defaults: project=examples/notes-angee, framework=.

    assert stack["sources"]["app"]["path"] == "../examples/notes-angee"
    assert stack["sources"]["framework"]["path"] == ".."
    build = stack["jobs"]["build"]
    assert build["workdir"] == "source://app"
    assert "--project" not in build["command"]
    postgres_ready = stack["jobs"]["postgres-ready"]["command"][-1]
    assert "uv run --extra postgres" in postgres_ready


def test_dev_stack_source_paths_translate_for_the_sibling_layout() -> None:
    """A downstream `.angee/` inside the project, framework a sibling checkout.

    The framework is not an ancestor of the project root, so every ``uv run``
    gains ``--project <framework>`` and the sibling source path walks up from
    ANGEE_ROOT.
    """

    stack = _render_dev_stack(project_path=".", framework_path="../angee-django")

    assert stack["sources"]["app"]["path"] == ".."
    assert stack["sources"]["framework"]["path"] == "../../angee-django"
    build = stack["jobs"]["build"]
    assert build["workdir"] == "source://app"
    assert build["command"][:4] == ["uv", "run", "--project", "../angee-django"]
    postgres_ready = stack["jobs"]["postgres-ready"]["command"][-1]
    assert "uv run --project ../angee-django --extra postgres" in postgres_ready


def test_dev_stack_keeps_absolute_source_paths_verbatim() -> None:
    """Absolute copier inputs are kept as-is (neither `../`-prefixed nor collapsed)."""

    stack = _render_dev_stack(project_path="/srv/project", framework_path="/opt/angee-django")

    assert stack["sources"]["app"]["path"] == "/srv/project"
    assert stack["sources"]["framework"]["path"] == "/opt/angee-django"
    assert stack["jobs"]["build"]["command"][:4] == ["uv", "run", "--project", "/opt/angee-django"]


def test_dev_stack_keeps_stack_answers_separate_from_workspace_answers() -> None:
    manifest = yaml.safe_load(DEV_COPIER.read_text(encoding="utf-8"))
    stack = _render_dev_stack()

    assert manifest["_answers_file"] == ".copier-answers.stack.yml"
    assert stack["template"]["answers_file"] == ".copier-answers.stack.yml"


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
