"""Exercise request visibility and holder authorization on the composed graph."""

from pathlib import Path

import pytest
from django.db import connection

from tests.composed_host import run_composed_tests


@pytest.mark.postgresql
def test_intake_access(tmp_path: Path) -> None:
    run_composed_tests(
        tmp_path,
        "tests.native_intake_capture.NeedAccessTests",
        app=("angee.intake", "angee.projects_knowledge"),
        test_postgresql=connection.vendor == "postgresql",
    )


@pytest.mark.postgresql
@pytest.mark.parametrize("case", ("NeedViewAsAdminOnlyTests", "DenormalizedNeedViewAsAdminOnlyTests"))
def test_task_writers_see_requester_display_without_view_as(tmp_path: Path, case: str) -> None:
    run_composed_tests(
        tmp_path,
        f"tests.native_intake_capture.{case}",
        app="angee.intake",
        test_postgresql=connection.vendor == "postgresql",
    )
