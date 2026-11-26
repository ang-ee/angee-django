"""The merge gate's changed-path selection reaches the tests a change can break."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FULL_SUITE = ["tests", "src/angee/resources/tests"]


def reached(*changed: str) -> list[str]:
    """Run the selection as CI does: changed paths on stdin, test files on stdout."""

    result = subprocess.run(
        [sys.executable, "scripts/changed_path_selection.py"],
        cwd=ROOT, input="\n".join(changed), capture_output=True, text=True, check=True,
    )
    return result.stdout.split()


def test_a_change_reaches_its_test_files_their_importers_and_composed_profiles() -> None:
    tests = reached("tests/test_stack.py", "src/angee/provenance/managers.py", "tests/native_intake_capture.py")

    assert {"tests/test_stack.py", "tests/test_provenance_native.py", "tests/test_composed_intake.py"} <= set(tests)
    assert "tests/test_documentation_contracts.py" not in tests


def test_a_template_reaches_the_tests_that_render_or_read_the_templates() -> None:
    tests = reached("templates/projects/web/template/settings.py")

    assert {"tests/test_stack_templates.py", "tests/test_addon_templates.py", "tests/test_host_build.py"} <= set(tests)
    assert "tests" not in tests


@pytest.mark.parametrize("changed", ["tests/conftest.py", "src/angee/storage/permissions.zed", "uv.lock"])
def test_an_input_every_test_reads_selects_the_full_suite(changed: str) -> None:
    assert reached(changed) == ["tests", "src/angee/resources/tests"]


def test_docs_and_web_code_reach_no_python_test() -> None:
    assert reached("docs/checks.md", "packages/refine/src/provider.ts") == []


@pytest.mark.parametrize(
    "changed", ["src/angee/base/checks.py", "src/angee/server/urls.py", "src/angee/graphql/records.py"],
)
def test_a_shared_owner_selects_the_full_suite(changed: str) -> None:
    assert reached(changed) == FULL_SUITE


def test_a_module_every_test_loads_selects_the_full_suite() -> None:
    """tests/conftest.py imports it, and the repository's conftest imports tests/conftest.py for every test."""

    assert reached("src/angee/checklists/testing/models.py") == FULL_SUITE


@pytest.mark.parametrize("changed", ["src/angee/projects/removed_module.py", ".agents/tools/owner-map-guard.py"])
def test_a_python_file_outside_the_import_graph_selects_the_full_suite(changed: str) -> None:
    assert reached(changed) == FULL_SUITE


def test_a_compose_migration_template_reaches_the_tests_that_import_it() -> None:
    tests = reached("src/angee/projects/compose_migrations/projects/task_recurrence_due_rule.py")

    assert "tests/test_projects_due_rules.py" in tests
    assert tests != FULL_SUITE
