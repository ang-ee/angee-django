"""Contracts of the Copier addon templates: one complete source addon and a consumer repository root.

The operator lists addon templates but does not render them, so these render
with the ``copier`` CLI when it is on the path and otherwise pin the template
sources: the question set, the rendered file set and the shapes that the
reference addons (``addons/angee/tags``, ``examples/addons/example/notes``)
establish.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ADDON_TEMPLATE = ROOT / "templates" / "addons" / "web"
REPOSITORY_TEMPLATE = ROOT / "templates" / "addons" / "repository"
WEB_DIR = "{% if include_web %}web{% endif %}"
TESTING_DIR = '{% if test_host == "bare" %}testing{% endif %}'
TESTS_DIR = '{% if test_host == "composed" %}tests{% endif %}'
BARE_TEST = '{% if test_host == "bare" %}test_{{ addon_id }}.py{% endif %}'

ADDON_FILES = {
    "__init__.py",
    "pyproject.toml",
    "models.py",
    "permissions.zed",
    "schema.py",
    "resources/demo/010_{{ addon_id }}.{{ model_name | lower }}.yaml",
}
# A consumer addon (`test_host=composed`) keeps its tests inside the addon: it
# reads its runtime model through `apps.get_model`, registers nothing, and
# consumer CI collects the package in place.
COMPOSED_FILES = {
    f"{TESTS_DIR}/__init__.py",
    TESTS_DIR + "/test_{{ addon_id }}.py",
}
# A framework addon (`test_host=bare`) renders the concrete test model the bare
# host registers, and its test at the addon root: pytest `testpaths` collects
# only the repository `tests`, so adoption moves the module there.
TESTING_FILES = {
    f"{TESTING_DIR}/__init__.py",
    f"{TESTING_DIR}/models.py",
    BARE_TEST,
}
WEB_FILES = {
    f"{WEB_DIR}/package.json",
    f"{WEB_DIR}/src/i18n.ts",
    f"{WEB_DIR}/src/index.test.ts",
    f"{WEB_DIR}/src/index.tsx",
    WEB_DIR + "/src/views/{{ model_name }}Page.tsx",
    f"{WEB_DIR}/tsconfig.json",
    f"{WEB_DIR}/vitest.config.ts",
}


def _template_files(template: Path) -> set[str]:
    source = template / "template"
    return {
        str(path.relative_to(source)).removesuffix(".jinja")
        for path in source.rglob("*")
        if path.is_file() or path.is_symlink()
    }


def test_addon_template_renders_one_complete_source_addon() -> None:
    copier = yaml.safe_load((ADDON_TEMPLATE / "copier.yml").read_text(encoding="utf-8"))
    assert copier["_angee"]["kind"] == "addon"
    assert copier["_subdirectory"] == "template"
    questions = {key for key in copier if not key.startswith("_")}
    assert questions == {
        "addon_id",
        "addon_name",
        "addon_title",
        "model_name",
        "route_path",
        "test_host",
        "include_web",
        "package_name",
    }
    assert copier["package_name"]["when"] == "{{ include_web }}"
    assert copier["test_host"]["default"] == "composed"
    assert copier["test_host"]["choices"] == ["composed", "bare"]
    assert _template_files(ADDON_TEMPLATE) == ADDON_FILES | COMPOSED_FILES | TESTING_FILES | WEB_FILES

    # The route claims the canonical `<label>.<Model>` label, never a bare model name.
    index = (ADDON_TEMPLATE / "template" / WEB_DIR / "src" / "index.tsx.jinja").read_text(encoding="utf-8")
    assert '"{{ addon_id }}.{{ model_name }}",' in index
    manifest = (ADDON_TEMPLATE / "template" / "pyproject.toml.jinja").read_text(encoding="utf-8")
    assert '"angee-base>=3,<4",' in manifest
    assert '{% if include_web %}\n  "angee-web>=3,<4",' in manifest
    model = (ADDON_TEMPLATE / "template" / "models.py.jinja").read_text(encoding="utf-8")
    for declaration in (
        "runtime = True",
        "sqid_prefix = ",
        'rebac_resource_type = "{{ addon_id }}/{{ model_name | lower }}"',
    ):
        assert declaration in model
    gate = (ADDON_TEMPLATE / "template" / "permissions.zed.jinja").read_text(encoding="utf-8")
    for arm in (
        "relation owner: auth/user // rebac:field=owner",
        "permission write__owner = transfer",
        "rebac:const=admin",
    ):
        assert arm in gate
    # The bare-host registration contract is prose at its owner: the testing package
    # docstring names the framework-repository files, and the template emits none of them.
    adoption = (ADDON_TEMPLATE / "template" / TESTING_DIR / "__init__.py.jinja").read_text(encoding="utf-8")
    assert "``tests/settings.py`` ``INSTALLED_APPS``" in adoption
    assert "``tests/conftest.py``" in adoption
    assert "repository's ``tests/test_{{ addon_id }}.py``" in adoption


def test_repository_template_renders_a_consumer_addon_root() -> None:
    copier = yaml.safe_load((REPOSITORY_TEMPLATE / "copier.yml").read_text(encoding="utf-8"))
    assert copier["_angee"]["kind"] == "addon"
    assert copier["_preserve_symlinks"] is True
    assert _template_files(REPOSITORY_TEMPLATE) == {"AGENTS.md", "CLAUDE.md", "addons/{{ addon_namespace }}/.gitkeep"}
    claude = REPOSITORY_TEMPLATE / "template" / "CLAUDE.md"
    assert claude.is_symlink() and claude.readlink() == Path("AGENTS.md")

    body = (REPOSITORY_TEMPLATE / "template" / "AGENTS.md.jinja").read_text(encoding="utf-8")
    assert len(body.splitlines()) <= 90
    instructions = " ".join(body.split())
    # Skills are reached by explicit path (`.agents/README.md`), never discovered by name.
    skill = "../angee/.agents/skills/{name}/SKILL.md"
    for contract in (
        "Declared seams only.",
        "No framework edits.",
        "No monkey-patching composed models.",
        "Product vocabulary stays in the addon.",
        "## Builder Role",
        "`../angee/.agents/skills/<skill>/SKILL.md`",
        "`../angee/addons/angee/<addon>/skills/<guide>/SKILL.md`",
        "`../angee/addons/angee/jobs/skills/jobs-tasks/SKILL.md`",
        f"1. [`angee-design`]({skill.format(name='angee-design')})",
        f"2. [`angee-addon`]({skill.format(name='angee-addon')})",
        "`test_host=composed`",
        f"3. [`angee-models`]({skill.format(name='angee-models')}) + [`angee-permissions`]",
        f"4. [`angee-graphql`]({skill.format(name='angee-graphql')}) + [`angee-resources`]",
        f"5. [`angee-forms`]({skill.format(name='angee-forms')}) + [`angee-pages`]",
        f"[`angee-tests`]({skill.format(name='angee-tests')}) authors the tests beside each artifact",
        f"6. [`angee-project`]({skill.format(name='angee-project')})",
        f"7. [`angee-runtime`]({skill.format(name='angee-runtime')})",
        "8. Acceptance",
        f"[`slice`]({skill.format(name='slice')})",
        "[`docs/checks.md`](../angee/docs/checks.md)",
    ):
        assert contract in instructions
    for name in ("angee-permissions", "angee-resources", "angee-pages"):
        assert f"[`{name}`]({skill.format(name=name)})" in instructions
    assert "`testing/models.py`, fragment" not in instructions


def _render_addon(tmp_path: Path, *, include_web: bool, test_host: str) -> Path:
    """Render the addon template as ``acme.contacts`` into ``tmp_path/addons/acme/contacts``."""

    copier = shutil.which("copier")
    if copier is None:
        pytest.skip("Rendering the addon template requires the copier CLI.")
    # Copier reads a template inside a git checkout at a git ref; copy it out to render the working tree.
    source = tmp_path / "template"
    shutil.copytree(ADDON_TEMPLATE, source, symlinks=True)
    target = tmp_path / "addons" / "acme" / "contacts"
    result = subprocess.run(
        [
            copier,
            "copy",
            "--quiet",
            "--defaults",
            "--trust",
            "-d",
            "addon_id=contacts",
            "-d",
            "addon_name=acme.contacts",
            "-d",
            "addon_title=Contact Book",
            "-d",
            "model_name=Contact",
            "-d",
            "route_path=/contacts",
            "-d",
            f"test_host={test_host}",
            "-d",
            f"include_web={str(include_web).lower()}",
            str(source),
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return target


@pytest.mark.parametrize("test_host", ["composed", "bare"])
@pytest.mark.parametrize("include_web", [True, False])
def test_copier_renders_the_addon_template(tmp_path: Path, include_web: bool, test_host: str) -> None:
    """Rendered files parse: TOML manifest, YAML rows, JSON package, Python sources."""

    target = _render_addon(tmp_path, include_web=include_web, test_host=test_host)
    rendered = {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()}
    expected = {
        name.replace("{{ addon_id }}", "contacts").replace("{{ model_name | lower }}", "contact")
        for name in ADDON_FILES
    }
    if test_host == "bare":
        expected |= {
            name.replace(TESTING_DIR, "testing").replace(BARE_TEST, "test_contacts.py") for name in TESTING_FILES
        }
    else:
        expected |= {name.replace(TESTS_DIR, "tests").replace("{{ addon_id }}", "contacts") for name in COMPOSED_FILES}
    if include_web:
        expected |= {name.replace(WEB_DIR, "web").replace("{{ model_name }}", "Contact") for name in WEB_FILES}
    assert rendered == expected

    if test_host == "bare":
        # Not under the addon's `tests/`: the bare host collects only the repository
        # `tests` (pytest `testpaths`), and the module states that adoption contract.
        assert not (target / "tests").exists()
        test_module = (target / "test_contacts.py").read_text(encoding="utf-8")
        assert "from acme.contacts.testing.models import Contact" in test_module
        assert "composed_tables" in test_module
        for step in (
            "``tests/test_contacts.py``",
            '``"acme.contacts"`` in ``tests/settings.py`` ``INSTALLED_APPS``',
            "``acme.contacts.testing.models`` once from ``tests/conftest.py``",
        ):
            assert step in test_module
    else:
        test_module = (target / "tests/test_contacts.py").read_text(encoding="utf-8")
        assert 'Contact = apps.get_model("contacts", "Contact")' in test_module
        assert 'call_command("rebac", "sync", verbosity=0)' in test_module
        assert "TransactionTestCase" in test_module
        assert "testing" not in test_module

    manifest = tomllib.loads((target / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["project"]["name"] == "acme-contacts"
    assert manifest["project"]["entry-points"]["angee.addons"] == {"acme.contacts": "acme.contacts"}
    assert ("angee-web>=3,<4" in manifest["project"]["dependencies"]) is include_web
    assert manifest["tool"]["angee"]["resources"]["demo"][0]["path"] == "demo/010_contacts.contact.yaml"
    rows = yaml.safe_load((target / "resources/demo/010_contacts.contact.yaml").read_text(encoding="utf-8"))
    assert rows["_meta"] == {"model": "contacts.Contact"}
    assert len(rows["rows"]) == 1
    for path in target.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    if include_web:
        assert '"contacts.Contact",' in (target / "web/src/index.tsx").read_text(encoding="utf-8")


BOOT_SCRIPT = """
import importlib
import os
import sys

sys.path[:0] = [{addons!r}, {core!r}, {root!r}, {framework_addons!r}, {example_addons!r}]
os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"

import django
from django.apps import apps
from django.conf import settings

settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "acme.contacts"]
django.setup()

config = apps.get_app_config("contacts")
assert config.name == "acme.contacts", config.name
source = importlib.import_module("acme.contacts.models").Contact
assert source._meta.abstract and source._meta.app_label == "contacts", source._meta
if {test_host!r} == "bare":
    importlib.import_module("acme.contacts.testing.models")
    concrete = apps.get_model("contacts", "Contact")
    assert not concrete._meta.abstract, concrete
    assert concrete._meta.db_table == "test_contacts_contact", concrete._meta.db_table
else:
    # A consumer addon registers no concrete model: only a composed host emits one.
    try:
        apps.get_model("contacts", "Contact")
    except LookupError:
        pass
    else:
        raise AssertionError("composed-host scaffolding must not register a bare-host model")
print("booted")
"""


@pytest.mark.parametrize("test_host", ["composed", "bare"])
def test_rendered_addon_boots_on_the_bare_django_host(tmp_path: Path, test_host: str) -> None:
    """Django installs the rendered addon: its label resolves and its source model is abstract.

    Runs in a subprocess so the parent's Django registry stays untouched: the bare
    test settings are extended with the rendered label and ``django.setup()`` runs.
    """

    pytest.importorskip("django")
    target = _render_addon(tmp_path, include_web=False, test_host=test_host)
    script = textwrap.dedent(BOOT_SCRIPT).format(
        addons=str(target.parents[1]),
        root=str(ROOT),
        core=str(ROOT / "core"),
        framework_addons=str(ROOT / "addons"),
        example_addons=str(ROOT / "examples" / "addons"),
        test_host=test_host,
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=ROOT,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "tests.settings"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "booted" in result.stdout
