"""Test helpers for the identity addon's users, for any test host that installs iam.

Production settings and serving code must not depend on this test support.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model


def create_user(username: str) -> Any:
    """Create one plain test user whose password is its username."""

    return get_user_model().objects.create_user(username=username, password=username)
