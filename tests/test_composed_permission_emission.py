"""Exercise session permission emission in a generated host on both databases."""

from __future__ import annotations

import pytest
from django.db import connection

from tests.composed_host import run_composed_tests


@pytest.mark.postgresql
def test_composed_permission_emission(tmp_path) -> None:
    run_composed_tests(
        tmp_path,
        "tests.native_permission_emission",
        app="angee.spaces",
        test_postgresql=connection.vendor == "postgresql",
    )
