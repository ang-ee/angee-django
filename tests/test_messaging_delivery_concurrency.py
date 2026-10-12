"""Collect delivery's native PostgreSQL claim and moderation races."""

import pytest
from django.db import connection

from tests.composed_host import run_composed_tests

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL locks are required.")


def test_composed_messaging_delivery_concurrency(tmp_path):
    run_composed_tests(tmp_path, "tests.native_messaging_delivery_concurrency",
                       app=("angee.posts", "angee.mcp"), test_postgresql=True)
