"""Changed-path selection composes native app dependencies with pytest's own paths."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.changed_path_selection import AppGraph, addon_graph, changed_paths, collected, isolated_suites, select

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
    paths, shared = collected(ROOT, ())
    return graph, tuple(paths), shared


def test_isolated_addon_tests_exclude_shared_suites(framework) -> None:
    graph, paths, profiles = framework
    suites = isolated_suites(ROOT, graph.owners)
    assert list(suites.values()) == ["angee.uom"]
    shared, hosts = select(
        ROOT, [Path("src/angee/uom/tests/test_uom.py")], paths,
        graph, isolated=suites, legacy=True, shared_tests=profiles,
    )
    assert shared.mode == "empty"
    assert hosts.paths == ("angee.uom",)


def test_legacy_template_readers_and_profile_references_survive(framework) -> None:
    graph, paths, profiles = framework
    template = Path("templates/projects/web/template/settings.py")
    shared, _ = select(ROOT, [template], paths, graph, legacy=True, shared_tests=profiles)
    assert {
        "tests/test_stack_templates.py", "tests/test_addon_templates.py", "tests/test_host_build.py",
    } <= set(shared.paths)
    profile = Path("tests/native_intake_capture.py")
    shared, _ = select(ROOT, [profile], paths, graph, legacy=True, shared_tests=profiles)
    assert "tests/test_composed_intake.py" in shared.paths


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
        files[folder / "test_contract.py"] = "def test_contract(): pass\n"
    files[Path("fixture_addons/__init__.py")] = ""
    files[Path("fixture_addons/leaf/testing.py")] = "VALUE = 1\n"
    files[Path("fixture_addons/mixed/test_contract.py")] = (
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
        "fixture_addons/leaf/test_contract.py", "fixture_addons/consumer/test_contract.py",
        "tests/test_registry.py", profile.as_posix(),
    } <= set(shared.paths)
    assert "fixture_addons/base/test_contract.py" not in shared.paths


def test_test_helper_reaches_mixed_importer_without_a_runtime_edge(addon_repo) -> None:
    root, graph, paths = addon_repo
    shared, _ = select(root, [Path("fixture_addons/leaf/testing.py")], paths, graph)
    assert "fixture_addons/mixed/test_contract.py" in shared.paths
    assert "fixture_addons/base/test_contract.py" not in shared.paths


@pytest.mark.parametrize("name", ["permissions.extends.zed", "sample.edi.xml", "invoice.pdf", "documents.console.ts"])
def test_non_python_addon_inputs_broaden_the_shared_host(addon_repo, name: str) -> None:
    root, graph, paths = addon_repo
    shared, hosts = select(
        root, [Path("fixture_addons/leaf") / name], paths, graph, isolated=graph.owners,
    )
    assert shared.mode == "full"
    assert hosts.paths == ("fixture_addons.consumer", "fixture_addons.leaf")


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
