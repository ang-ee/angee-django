"""Shared roster data for the spaces campaign, using the existing composition."""

from types import SimpleNamespace

import pytest
from rebac import system_context

from angee.messaging.testing.models import Party, Person
from angee.spaces.testing.models import Group, Membership
from tests.conftest import create_platform_admin, create_user
from tests.test_spaces import _person_for, _schema

ROLES = ("owner", "moderator", "member", "viewer")
SEATS = (*ROLES, "pending", "dismissed", "outsider", "column_owner", "administrator")
MANAGERS = {"owner", "column_owner", "administrator"}
READERS = {*ROLES, "column_owner", "administrator"}


@pytest.fixture(params=("denormalized", "registry"))
def spaces_storage(request, settings, spaces_tables):
    """Reuse spaces' composed schema and exercise each native relationship store."""
    settings.REBAC_LOCAL_BACKEND_STORAGE = request.param
    return request.param


@pytest.fixture
def roster(spaces_storage):
    """Keep column ownership, roster authority, and inactive seats independent."""
    actors = {}
    people = {}
    for seat in SEATS:
        actors[seat], people[seat] = _person_for(f"campaign-{seat}")
    actors["administrator"] = create_platform_admin("campaign-admin")
    rows = {}
    with system_context(reason="spaces campaign roster setup"):
        group = Group.objects.create(name="Team", owner=actors["column_owner"], created_by=actors["column_owner"])
        for seat in (*ROLES, "pending", "dismissed"):
            rows[seat] = Membership.objects.create(
                group=group, party=people[seat], role=seat if seat in ROLES else "owner",
                is_confirmed=seat != "pending", is_dismissed=seat == "dismissed",
            )
    return SimpleNamespace(group=group, actors=actors, people=people, rows=rows)


@pytest.fixture
def spaces_console():
    """Build the same composed console schema as the existing spaces tests."""
    return _schema()


def target_party(name, actors=()):
    """Give GraphQL actors directory read, independently of roster authority."""
    user = create_user(name)
    with system_context(reason="spaces campaign target identity"):
        person = Person.objects.for_user(user)
        for actor in actors:
            Party.objects.get(pk=person.pk).grant_record_access("reader", actor)
    return person
