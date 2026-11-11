"""Ownership adopters declare the transfer gate, and raw owner writes require it."""

import pytest
from django.apps import apps
from rebac import PermissionDenied, actor_context, system_context
from rebac.roles import grant as grant_role

from angee.agents.testing.models import Agent
from angee.base.models.ownership import OwnerMixin
from angee.projects.testing.models import Project
from tests.messaging_campaign import grant
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user


def test_every_adopter_declares_owner_and_transfer_gate(composed_tables):
    """No composed adopter reports E022 or E027 on the merged schema; a failure names every offender.

    Composed adopters are the addons' models and the root conftest's compositions; a test
    module's own probe models install their own schema and are not adopters.
    """
    del composed_tables
    composed = ("angee.", "tests.conftest")
    adopters = [
        model for model in apps.get_models() if issubclass(model, OwnerMixin) and model.__module__.startswith(composed)
    ]
    assert {Project, Agent} <= set(adopters)
    offenders = {
        model._meta.label: [error.msg for error in model.check() if error.id in {"angee.E022", "angee.E027"}]
        for model in adopters
    }
    assert {label: errors for label, errors in offenders.items() if errors} == {}


def test_raw_owner_writes_require_transfer(campaign_access, campaign_user):
    """An editor's save, partial save or queryset update of the owner is refused; transfer moves it."""
    owner, writer, replacement = (campaign_user(role) for role in ("owner", "writer", "replacement"))
    with system_context(reason="tests.t3.owned_record"):
        row = Project.objects.create(title="Owned row", owner=owner, created_by=owner)
        grant(row, "editor", writer)
    assert row.with_actor(writer).has_access("write")
    assert not row.with_actor(writer).has_access("transfer")
    for field, value in (("owner", replacement), ("owner_id", replacement.pk)):
        for path in ("full_save", "partial_save", "queryset"):
            with actor_context(writer), pytest.raises(PermissionDenied):
                if path == "queryset":
                    Project.objects.filter(pk=row.pk).update(**{field: value})
                else:
                    candidate = Project.objects.get(pk=row.pk)
                    setattr(candidate, field, value)
                    candidate.save(**({"update_fields": (field,)} if path == "partial_save" else {}))
            assert Project._base_manager.get(pk=row.pk).owner_id == owner.pk, (field, path)
    with actor_context(owner):
        Project.objects.get(pk=row.pk).transfer_ownership(replacement)
    persisted = Project._base_manager.get(pk=row.pk)
    assert (persisted.owner_id, persisted.created_by_id) == (replacement.pk, owner.pk)


def test_an_agent_installed_without_a_user_is_claimed_only_through_transfer(campaign_access, campaign_user):
    """Install-time loads have no acting user; a platform administrator claims the agent afterwards."""
    admin, stranger = campaign_user("admin"), campaign_user("stranger")
    with system_context(reason="tests.t3.agent_install"):
        grant_role(actor=admin, role="angee/role:admin")
        agent = Agent.objects.create(name="Installed agent")
    assert agent.owner_id is None and agent.created_by_id is None
    with actor_context(stranger), pytest.raises(PermissionDenied):
        Agent._base_manager.get(pk=agent.pk).transfer_ownership(stranger)
    with actor_context(admin):
        Agent._base_manager.get(pk=agent.pk).transfer_ownership(admin)
    assert Agent._base_manager.get(pk=agent.pk).owner_id == admin.pk
