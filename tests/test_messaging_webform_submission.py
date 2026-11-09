"""Collect ChannelWebform submission contracts on a composed host; PostgreSQL adds the lock races."""

from django.db import connection

from tests.composed_host import run_composed_tests


def test_composed_webform_submission(tmp_path):
    run_composed_tests(
        tmp_path,
        "tests.native_messaging_webform_submission",
        app="angee.messaging",
        test_postgresql=connection.vendor == "postgresql",
    )
