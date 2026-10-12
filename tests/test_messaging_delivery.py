"""Collect delivery/feed contracts through the native composed-host owner."""

import pytest
from django.db import connection

from tests.composed_host import run_composed_tests


# One composed host per contract class keeps each run within the composed-host timeout.
@pytest.mark.parametrize("contracts", ["DeliveryContracts", "FeedDeliveryContracts"])
def test_composed_messaging_delivery(tmp_path, contracts):
    run_composed_tests(tmp_path, f"tests.native_messaging_delivery.{contracts}", app=("angee.posts", "angee.mcp"),
                       test_postgresql=connection.vendor == "postgresql")
