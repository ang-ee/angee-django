"""The merge gate's changed-path selection reaches the tests a change can break."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


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
