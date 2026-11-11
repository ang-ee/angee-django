"""Delivery, feed and webform contracts on emitted messaging models, one composed host per profile."""

from pathlib import Path

import pytest

from tests.composed_host import run_composed_tests


@pytest.mark.parametrize("profile", [
    pytest.param({
        "app": ("angee.posts", "angee.mcp"),
        "sqlite": ("tests.native_messaging_delivery",),
        # Claim, sweep and moderation races need independent PostgreSQL connections.
        "postgresql": ("tests.native_messaging_delivery", "tests.native_messaging_delivery_concurrency"),
    }, id="posts"),
    pytest.param({
        "app": "angee.messaging",
        "sqlite": ("tests.native_messaging_webform_submission",),
        "postgresql": ("tests.native_messaging_webform_submission",),
    }, id="messaging"),
])
def test_composed_messaging(tmp_path: Path, subtests: pytest.Subtests, profile: dict) -> None:
    run_composed_tests(tmp_path, subtests, **profile)
