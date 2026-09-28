"""Lane fixtures compose the existing source graph and deterministic identities."""

from hashlib import sha256

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context
from rebac.models import Relationship, RelationshipRegistry

from tests.test_messaging_access import messaging_access_schema as messaging_access_schema


@pytest.fixture
def campaign_access(messaging_access_schema, settings):
    """Keep both native stores and explicit administrator arms under test."""
    settings.REBAC_SUPERUSER_BYPASS = False
    return messaging_access_schema


@pytest.fixture
def campaign_user(request):
    """Names depend on the test ID and role, never collection order or randomness."""
    prefix = sha256(request.node.nodeid.encode()).hexdigest()[:12]

    def create(role, **fields):
        with system_context(reason="tests.t3.account"):
            return get_user_model().objects.create_user(
                username=f"t3-{prefix}-{role}",
                email=f"t3-{prefix}-{role}@example.test",
                **fields,
            )

    return create


def relationship_snapshot():
    """Observe both stores, including the inactive store, without writing grants."""
    return tuple(list(model._base_manager.order_by("pk").values()) for model in (Relationship, RelationshipRegistry))
