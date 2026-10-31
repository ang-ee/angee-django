"""Native row contracts introduced by the intelligence owner refactor."""

import pytest
from django.db import models

from angee.agents.provisioning import provision_agent
from angee.agents.testing.models import Agent


def test_provisioning_requires_a_persisted_agent() -> None:
    with pytest.raises(ValueError, match="A saved agent is required"):
        provision_agent(Agent(name="Unsaved agent"))


def test_agent_inherits_native_service_user_field_contract() -> None:
    field = Agent._meta.get_field("user")
    assert isinstance(field, models.OneToOneField)
    assert field.null and field.blank and field.editable
    assert field.remote_field.on_delete is models.PROTECT
    assert field.remote_field.related_name == "agent"
    assert Agent.service_user_prefix == "agent"
