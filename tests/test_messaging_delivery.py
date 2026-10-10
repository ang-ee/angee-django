"""Collect delivery/feed contracts through the native composed-host owner."""

from django.db import connection

from tests.composed_host import run_composed_tests


def test_composed_messaging_delivery(tmp_path):
    run_composed_tests(tmp_path, "tests.native_messaging_delivery", app=("angee.posts", "angee.mcp"),
                       test_postgresql=connection.vendor == "postgresql")
