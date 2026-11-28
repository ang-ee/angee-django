"""Changed-path selection composes native app dependencies with pytest's own paths."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import changed_path_selection
from scripts.changed_path_selection import (
    AppGraph,
    additive_addons,
    addon_graph,
    changed_paths,
    collected,
    isolated_suites,
    select,
    shared_pytest_args,
    web_plan,
)
from scripts.postgres_selection import entries, groups, kept

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/changed_path_selection.py"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", *args], cwd=root, text=True,
    ).strip()


def commit(root: Path) -> str:
    git(root, "add", ".")
    git(root, "commit", "--no-gpg-sign", "-m", "inputs")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def framework():
    graph = addon_graph(ROOT, ())
    paths, shared, _ = collected(ROOT, ())
    return graph, tuple(paths), shared


@pytest.mark.parametrize(("changes", "expected_hosts"), [
    (["src/angee/uom/tests/test_uom.py"], ("angee.uom",)),
    (["docs/checks.md"], ()),
    ([], ()),
])
def test_changes_without_shared_inputs_exclude_shared_suites(framework, changes, expected_hosts) -> None:
    graph, paths, profiles = framework
    suites = isolated_suites(ROOT, graph.owners)
    assert list(suites.values()) == ["angee.uom"]
    shared, hosts = select(
        ROOT, list(map(Path, changes)), paths,
        graph, isolated=suites, legacy=True, shared_tests=profiles,
    )
    assert shared.mode == "empty"
    assert hosts.paths == expected_hosts
    postgres = entries()
    chosen = kept(postgres, shared.paths)
    assert chosen == []
    assert groups(postgres, chosen, {}) == []


def test_legacy_template_readers_and_profile_references_survive(framework) -> None:
    graph, paths, profiles = framework
    template = Path("templates/projects/web/template/settings.py")
    shared, _ = select(ROOT, [template], paths, graph, legacy=True, shared_tests=profiles)
    assert {
        "tests/test_stack_templates.py", "tests/test_addon_templates.py", "tests/test_host_build.py",
    } <= set(shared.paths)
    assert "tests/test_composed_examples.py" not in kept(entries(), shared.paths)
    profile = Path("tests/native_intake_capture.py")
    shared, _ = select(ROOT, [profile], paths, graph, legacy=True, shared_tests=profiles)
    assert "tests/test_composed_intake.py" in shared.paths


def test_addon_frontend_edit_reaches_its_python_reader(framework) -> None:
    graph, paths, profiles = framework
    action = Path("src/angee/messaging_integrate_imap/web/src/ImportImapSampleAction.tsx")
    shared, _ = select(ROOT, [action], paths, graph, legacy=True, shared_tests=profiles)
    assert shared.mode == "selected"
    assert "tests/test_messaging_imap.py" in shared.paths
    assert "tests/test_composed_examples.py" not in shared.paths


def test_calendar_change_reaches_global_composition_readers(framework) -> None:
    graph, paths, profiles = framework
    change = Path("src/angee/calendar/models.py")
    shared, _ = select(ROOT, [change], paths, graph, legacy=True, shared_tests=profiles)
    assert "tests/test_registry_contracts.py" in shared.paths
    assert "tests/test_composed_examples.py" not in shared.paths


@pytest.mark.parametrize(("change", "mode"), [
    ("examples/src/example/notes/schema.py", "selected"),
    ("src/angee/storage/permissions.zed", "full"),
    ("tests/native_graphql_document_budgets.py", "selected"),
    ("examples/src/example/notes/tests/test_workflow_steps.py", "selected"),
])
def test_postgres_examples_profile_runs_for_its_inputs(framework, change: str, mode: str) -> None:
    graph, paths, profiles = framework
    shared, _ = select(ROOT, [Path(change)], paths, graph, legacy=True, shared_tests=profiles)
    assert shared.mode == mode
    postgres = entries()
    chosen = postgres if shared.mode == "full" else kept(postgres, shared.paths)
    assert "tests/test_composed_examples.py" in chosen


@pytest.mark.parametrize("path", [
    "uv.lock", ".python-version", "tests/conftest.py", "src/angee/calendar/apps.py",
    "src/angee/storage/permissions.zed", "src/angee/projects/removed_module.py",
    "src/angee/checklists/testing/models.py", ".agents/tools/owner-map-guard.py",
])
def test_legacy_fallback_preserves_shared_inputs(framework, path: str) -> None:
    graph, paths, profiles = framework
    shared, _ = select(ROOT, [Path(path)], paths, graph, legacy=True, shared_tests=profiles)
    assert shared.mode == "full"
    assert shared.reason


@pytest.fixture()
def addon_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An isolated import graph with a prerequisite, leaf, consumer, and extra test-only importer."""
    owners = {}
    files = {}
    for name in ("base", "leaf", "consumer", "mixed"):
        folder = Path("fixture_addons") / name
        owners[folder] = f"fixture_addons.{name}"
        files[folder / "__init__.py"] = ""
        files[folder / "models.py"] = ""
        files[folder / "pytest.ini"] = "[pytest]\ntestpaths = tests\n"
        files[folder / "tests" / "__init__.py"] = ""
        files[folder / "tests" / "test_contract.py"] = "def test_contract(): pass\n"
    files[Path("fixture_addons/__init__.py")] = ""
    files[Path("fixture_addons/leaf/testing.py")] = "VALUE = 1\n"
    files[Path("fixture_addons/mixed/tests/test_contract.py")] = (
        "from fixture_addons.leaf.testing import VALUE\n\ndef test_contract(): assert VALUE == 1\n"
    )
    files[Path("tests/__init__.py")] = ""
    files[Path("tests/test_registry.py")] = (
        "def test_contract():\n    return apps.get_model('leaf_label', 'Record')\n"
    )
    files[Path("tests/test_profile.py")] = "def test_contract(): pass\n"
    for path, content in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    monkeypatch.syspath_prepend(str(tmp_path))
    graph = AppGraph(
        owners, {"fixture_addons.leaf": ["fixture_addons.base"], "fixture_addons.consumer": ["fixture_addons.leaf"]},
        {"leaf_label": "fixture_addons.leaf"},
    )
    paths = [path for path in files if path.name.startswith("test_")]
    return tmp_path, graph, paths


def test_selects_changed_leaf_and_reverse_consumers_without_its_prerequisite(addon_repo) -> None:
    root, graph, paths = addon_repo
    profile = Path("tests/test_profile.py")
    shared, _ = select(
        root, [Path("fixture_addons/leaf/models.py")], paths, graph, shared_tests=[profile],
    )
    assert shared.mode == "selected"
    assert {
        "fixture_addons/leaf/tests/test_contract.py", "fixture_addons/consumer/tests/test_contract.py",
        "tests/test_registry.py", profile.as_posix(),
    } <= set(shared.paths)
    assert "fixture_addons/base/tests/test_contract.py" not in shared.paths


def test_test_helper_reaches_mixed_importer_without_a_runtime_edge(addon_repo) -> None:
    root, graph, paths = addon_repo
    shared, _ = select(root, [Path("fixture_addons/leaf/testing.py")], paths, graph)
    assert "fixture_addons/mixed/tests/test_contract.py" in shared.paths
    assert "fixture_addons/base/tests/test_contract.py" not in shared.paths


def test_owned_resource_and_inactive_permissions_select_actual_readers(addon_repo) -> None:
    root, graph, paths = addon_repo
    owner = Path("fixture_addons/leaf")
    for change in (owner / "resources/demo/010_record.yaml", owner / "permissions.extends.zed"):
        shared, _ = select(root, [change], paths, graph, shared_apps=frozenset())
        assert shared.mode == "selected"
        assert "tests/test_registry.py" in shared.paths
        assert "fixture_addons/base/tests/test_contract.py" not in shared.paths
    shared, _ = select(
        root, [owner / "permissions.extends.zed"], paths, graph, shared_apps=frozenset({"fixture_addons.leaf"}),
    )
    assert shared.mode == "full"


def test_native_shared_args_exclude_standalone_suite_even_when_explicit(addon_repo) -> None:
    root, graph, _ = addon_repo
    addon = Path("fixture_addons/leaf")
    tests = addon / "tests"
    args = shared_pytest_args(root, ["tests", str(tests / "test_contract.py")], {addon: graph.owners[addon]})
    assert args == ["tests", "--ignore", str(root / tests)]


def test_isolated_test_helper_can_reach_a_legacy_consumer_before_collection(addon_repo) -> None:
    root, graph, _ = addon_repo
    test = Path("fixture_addons/leaf/tests/test_contract.py")
    (root / "fixture_addons/mixed/pytest.ini").unlink()
    suites = isolated_suites(root, graph.owners)
    shared, _ = select(root, [test], [], graph, isolated=suites, preflight=True)
    assert shared.mode == "empty"
    (root / "fixture_addons/mixed/tests/test_contract.py").write_text(
        "from fixture_addons.leaf.tests import test_contract\n"
    )
    shared, hosts = select(root, [test], [], graph, isolated=suites, preflight=True)
    assert shared.mode == "selected"
    assert hosts.paths == ("fixture_addons.leaf",)


def test_package_edit_reaches_native_reverse_fragments(tmp_path, monkeypatch) -> None:
    library = Path("packages/library")
    consumer = Path("src/angee/consumer")
    web = consumer / "web"
    monkeypatch.setattr(changed_path_selection, "pnpm_projects", lambda root, projects=(): {library, web})
    graph = AppGraph({consumer: "angee.consumer"}, {}, {})
    packages, stack = web_plan(
        tmp_path, [library / "src/index.ts"], graph, {},
        base=None, fallback="", additions={}, graph_fallback="",
    )
    assert packages.paths == (library.as_posix(),)
    assert stack.paths == ("angee.consumer",)


@pytest.mark.parametrize("existing_drift", [False, True])
def test_additive_rendered_lock_reaches_only_new_stack_project(tmp_path, existing_drift) -> None:
    git(tmp_path, "init")
    lock = Path(".github/ci/pnpm-lock.yaml")
    (tmp_path / lock).parent.mkdir(parents=True)
    (tmp_path / lock).write_text("importers:\n  .: {}\n")
    base = commit(tmp_path)
    addon = Path("src/arp/new")
    web = addon / "web"
    (tmp_path / web).mkdir(parents=True)
    project = web / "package.json"
    (tmp_path / project).write_text("{}\n")
    existing = "{ changed: true }" if existing_drift else "{}"
    importer = Path("workspaces/src") / tmp_path.name / web
    (tmp_path / lock).write_text(f"importers:\n  .: {existing}\n  {importer.as_posix()}: {{}}\n")
    additions = {addon: "arp.new"}
    packages, stack = web_plan(
        tmp_path, [lock, project], AppGraph(additions, {}, {}), additions,
        base=base, fallback="", additions=additions, graph_fallback="",
    )
    assert packages.mode == "empty"
    assert stack.mode == ("full" if existing_drift else "selected")
    assert stack.paths == (() if existing_drift else ("arp.new",))


@pytest.mark.parametrize("delta", ["local", "plugin", "prior changed", "unattributed"])
def test_additive_workspace_requires_native_attribution(tmp_path, monkeypatch, delta) -> None:
    git(tmp_path, "init")
    manifest = tmp_path / "pyproject.toml"
    definition = """
[project]
dependencies = {requirements}
[tool.uv.sources]
{sources}
[tool.uv.workspace]
members = {members}
"""
    manifest.write_text(definition.format(requirements="[]", sources="", members="[]"))
    base = commit(tmp_path)
    owner = Path("src/demo/new")
    (tmp_path / owner / "tests").mkdir(parents=True)
    (tmp_path / owner / "tests/__init__.py").touch()
    (tmp_path / owner / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    (tmp_path / owner / "pyproject.toml").touch()
    manifest.write_text(definition.format(
        requirements='["demo-new==1"]', sources="demo-new = { workspace = true }", members='["src/demo/new"]',
    ))
    commit(tmp_path)
    distribution = SimpleNamespace(version="1", entry_points=SimpleNamespace(select=lambda **kwargs: ()))
    monkeypatch.setattr("django_compose.components.installed_components", lambda group: SimpleNamespace(
        providers={"demo.new": "demo-new"}, distributions={"demo-new": distribution},
    ))
    packages = [{"name": "prior", "version": "1"}]
    new = {"name": "demo-new", "directory": {"path": owner.as_posix(), "editable": True}}
    plugin = {"name": "new-plugin"}

    def export(root, extra, *, prune=()):
        if root != tmp_path:
            return {"packages": packages.copy()}
        if prune:
            assert prune == ("demo-new",)
            return {"packages": [*packages, plugin] if delta == "unattributed" else packages.copy()}
        prior = [{"name": "prior", "version": "2"}] if delta == "prior changed" else packages
        return {"packages": [*prior, new, *([plugin] if delta == "plugin" else [])]}

    monkeypatch.setattr(changed_path_selection, "uv_export", export)
    monkeypatch.setattr(changed_path_selection.metadata, "distribution", lambda name: SimpleNamespace(
        entry_points=SimpleNamespace(select=lambda group: (True,) if group == "pytest11" else ()), files=(),
    ))
    suites = {owner: "demo.new"}
    graph = AppGraph(suites, {}, {})
    result = additive_addons(
        tmp_path, base, [Path("pyproject.toml"), owner / "pyproject.toml"], graph, suites,
        apps=(), extra=(), donor=None,
    )
    assert result == (suites if delta == "local" else {})


def test_migration_template_namespace_does_not_break_the_native_module_graph(addon_repo) -> None:
    root, graph, paths = addon_repo
    migration = Path("fixture_addons/leaf/compose_migrations/preflight.py")
    (root / migration.parent).mkdir()
    (root / migration).touch()
    source = Path("fixture_addons/leaf/models.py")
    shared, _ = select(root, [source], paths, graph)
    assert shared.mode == "selected"
    shared, _ = select(root, [migration], paths, graph)
    assert shared.mode == "full"


def test_custom_native_layout_broadens_host_selection(addon_repo) -> None:
    root, graph, paths = addon_repo
    (root / "fixture_addons/consumer/pytest.ini").write_text("[pytest]\ntestpaths = checks\n")
    shared, hosts = select(
        root, [Path("fixture_addons/leaf/tests/test_contract.py")], paths, graph, isolated=graph.owners,
    )
    assert shared.paths == ("fixture_addons/leaf/tests/test_contract.py",)
    assert hosts.mode == "full"
    assert hosts.reason == "custom isolated test layout"


@pytest.mark.parametrize("change", ["fixture_addons/leaf/host_fixtures.py", "fixture_addons/leaf/web/widget.tsx"])
def test_declared_isolated_plugin_reaches_its_host_without_a_runtime_edge(addon_repo, change: str) -> None:
    root, graph, paths = addon_repo
    fixtures = Path("fixture_addons/leaf/host_fixtures.py")
    (root / fixtures).write_text("import pytest\n")
    (root / "fixture_addons/mixed/tests/test_contract.py").write_text("def test_contract(): pass\n")
    (root / "fixture_addons/mixed/conftest.py").write_text(
        "pytest_plugins = ('fixture_addons.leaf.host_fixtures',)\n",
    )
    _, hosts = select(root, [Path(change)], paths, graph, isolated=graph.owners)
    assert "fixture_addons.mixed" in hosts.paths
    assert "fixture_addons.base" not in hosts.paths


@pytest.mark.parametrize("name", ["permissions.extends.zed", "sample.edi.xml", "invoice.pdf", "documents.console.ts"])
def test_non_python_addon_inputs_broaden_the_shared_host(addon_repo, name: str) -> None:
    root, graph, paths = addon_repo
    shared, hosts = select(
        root, [Path("fixture_addons/leaf") / name], paths, graph, isolated=graph.owners,
    )
    assert shared.mode == "full"
    assert hosts.paths == ("fixture_addons.consumer", "fixture_addons.leaf", "fixture_addons.mixed")


def test_explicit_shared_reader_runs_for_frontend_changes_but_prose_is_empty(addon_repo) -> None:
    root, graph, paths = addon_repo
    profile = Path("tests/test_profile.py")
    shared, _ = select(root, [Path("web/widget.tsx")], paths, graph, shared_tests=[profile])
    assert shared.paths == (profile.as_posix(),)
    shared, hosts = select(root, [Path("docs/checks.md")], paths, graph, shared_tests=[profile])
    assert shared.mode == hosts.mode == "empty"


def test_exact_diff_handles_missing_baselines_and_deletions(tmp_path: Path) -> None:
    git(tmp_path, "init")
    module = tmp_path / "test_demo.py"
    module.write_text("def test_demo(): pass\n")
    base = commit(tmp_path)
    module.write_text("def test_demo(): assert True\n")
    head = commit(tmp_path)
    assert changed_paths(tmp_path, base, head) == ([Path(module.name)], "")
    assert "base" in changed_paths(tmp_path, "1" * 40, head)[1]
    assert "head" in changed_paths(tmp_path, base, base)[1]
    module.unlink()
    assert "removed" in changed_paths(tmp_path, base, commit(tmp_path))[1]


def test_cli_preserves_pytest_patterns_and_skips_an_intentionally_empty_selection(tmp_path: Path) -> None:
    git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    docs = tmp_path / "README.md"
    docs.write_text("before\n")
    base = commit(tmp_path)
    (tmp_path / "testWithoutUnderscore.py").write_text("def test_selected(): pass\n")
    head = commit(tmp_path)
    command = [
        sys.executable, str(SCRIPT), "pytest", "--root", str(tmp_path), "--base", base, "--head", head,
        "--", "-p", "no:django", "-o", "python_files=test*.py", "-q",
    ]
    run = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=True)
    record = json.loads(run.stdout.splitlines()[0])
    assert record["paths"] == ["testWithoutUnderscore.py"]
    assert "1 passed" in run.stdout
    docs.write_text("after\n")
    command[command.index(base)] = head
    command[command.index("--head") + 1] = commit(tmp_path)
    run = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=True)
    record = json.loads(run.stdout)
    assert record["mode"] == "empty"
    assert record["paths"] == []


def test_consumer_collects_with_callers_prepopulated_registry_and_import_paths(tmp_path: Path) -> None:
    git(tmp_path, "init")
    tests, plugins = tmp_path / "tests", tmp_path / "plugins"
    tests.mkdir()
    plugins.mkdir()
    (tests / "__init__.py").touch()
    (plugins / "uses_registry.py").write_text("from django.apps import apps\nassert apps.ready\n")
    (tmp_path / "review_settings.py").write_text("SECRET_KEY = 'test'\nINSTALLED_APPS = []\n")
    module = tests / "test_contract.py"
    module.write_text("def test_contract(): pass\n")
    (tests / "test_other.py").write_text("def test_other(): pass\n")
    base = commit(tmp_path)
    module.write_text("def test_contract(): assert True\n")
    head = commit(tmp_path)
    launcher = """
import os
import sys
from pathlib import Path

import django

script_dir, base, head = sys.argv[1:]
sys.path[:0] = [script_dir, str(Path.cwd() / "plugins")]
os.environ["DJANGO_SETTINGS_MODULE"] = "review_settings"
django.setup()

from changed_path_selection import run_selected

raise SystemExit(run_selected(
    ["-p", "django", "-p", "uses_registry", "-p", "no:randomly", "-q"],
    root=Path.cwd(), base=base, head=head, apps=("django.contrib.contenttypes",),
))
"""
    command = [sys.executable, "-c", launcher, str(SCRIPT.parent)]
    for diff, mode, count in (([base, head], "selected", 1), (["", ""], "full", 2)):
        run = subprocess.run([*command, *diff], cwd=tmp_path, capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stdout + run.stderr
        assert json.loads(run.stdout.splitlines()[0])["mode"] == mode, run.stdout
        assert f"{count} passed" in run.stdout
