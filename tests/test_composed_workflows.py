"""Shipped workflow graphs against fresh generated models, one composed host per profile."""

from pathlib import Path

import pytest

from tests.composed_host import run_composed_tests


@pytest.mark.parametrize(("app", "label", "on_postgresql"), [
    pytest.param(
        ("angee.workflows_posts", "angee.workflows_messaging", "angee.agents_runtime_pydantic", "angee.knowledge"),
        "tests.native_workflows_posts", True, id="posts",
    ),
    pytest.param("angee.workflows_messaging", "tests.native_workflows_messaging", True, id="messaging"),
    pytest.param("angee.workflows_parties", "tests.native_workflows_parties", True, id="parties"),
    pytest.param("angee.workflows_extraction", "tests.native_extraction_workflow", True, id="extraction"),
    pytest.param("angee.workflows_integrate", "tests.native_integrate_workflow", False, id="integrate"),
])
def test_composed_workflows(
    tmp_path: Path, subtests: pytest.Subtests, app: str | tuple[str, ...], label: str, on_postgresql: bool,
) -> None:
    run_composed_tests(tmp_path, subtests, app=app, sqlite=(label,), postgresql=(label,) if on_postgresql else ())
