"""Tests for the member-scoped ``colleagues`` people surface.

Unlike the platform-admin ``users`` catalogue, ``colleagues`` is available to a
plain signed-in member and is scoped by REBAC company membership: it returns only
the active users who share a company of record with the actor. It backs the
consumer pickers (chatter recipient suggestions, the discuss person picker) that
the admin-gated catalogue cannot serve.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from rebac import (
    RelationshipTuple,
    app_settings,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.roles import grant

from tests.conftest import addon_schema, execute_schema, graphql_request, result_data

User = get_user_model()
iam_schema = importlib.import_module("angee.iam.schema")

_COLLEAGUES = """
    query Colleagues($search: String, $limit: Int) {
      colleagues(search: $search, limit: $limit) {
        id
        username
        display_name
        email
        is_active
      }
    }
"""


def _console_schema() -> Any:
    """Build the IAM-only console schema the runtime composes ``colleagues`` into."""

    return addon_schema(iam_schema.schemas, "console")


def _grant_membership(company: Any, user: Any) -> None:
    """Write one ``direct_member`` company-membership tuple for ``user``."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(company),
                relation="direct_member",
                subject=to_subject_ref(user),
            )
        ]
    )


def _colleagues(actor: Any, *, search: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Execute the ``colleagues`` query as ``actor`` and return its rows."""

    data = result_data(
        execute_schema(
            _console_schema(),
            _COLLEAGUES,
            {"search": search, "limit": limit},
            request=graphql_request(actor),
        )
    )
    return list(data["colleagues"])


@pytest.mark.django_db
def test_member_sees_co_members_across_their_companies_only() -> None:
    """A member's colleagues span every company they belong to — and no further."""

    call_command("rebac", "sync", verbosity=0)
    company_model = apps.get_model("iam", "Company")
    actor = User.objects.create_user(username="actor", email="actor@example.com")
    peer_x = User.objects.create_user(username="peer-x", email="x@example.com")
    peer_y = User.objects.create_user(username="peer-y", email="y@example.com")
    outsider = User.objects.create_user(username="outsider", email="out@example.com")
    with system_context(reason="test colleagues setup"):
        company_x = company_model.objects.create(name="Company X")
        company_y = company_model.objects.create(name="Company Y")
        company_z = company_model.objects.create(name="Company Z")
    _grant_membership(company_x, actor)
    _grant_membership(company_x, peer_x)
    _grant_membership(company_y, actor)
    _grant_membership(company_y, peer_y)
    _grant_membership(company_z, outsider)

    usernames = {row["username"] for row in _colleagues(actor)}

    # Co-members of both of the actor's companies — never the actor themselves,
    # and never a user (outsider) from a company the actor is not a member of.
    assert usernames == {"peer-x", "peer-y"}


@pytest.mark.django_db
def test_member_with_no_company_sees_no_colleagues() -> None:
    """A member of no company has no colleagues — the surface is never platform-wide."""

    call_command("rebac", "sync", verbosity=0)
    loner = User.objects.create_user(username="loner", email="loner@example.com")
    User.objects.create_user(username="somebody", email="somebody@example.com")

    assert _colleagues(loner) == []


@pytest.mark.django_db
def test_inactive_co_members_are_excluded() -> None:
    """A deactivated co-member drops out of the colleague list."""

    call_command("rebac", "sync", verbosity=0)
    company_model = apps.get_model("iam", "Company")
    actor = User.objects.create_user(username="active-actor", email="aa@example.com")
    active = User.objects.create_user(username="active-peer", email="ap@example.com")
    inactive = User.objects.create_user(
        username="inactive-peer", email="ip@example.com", is_active=False
    )
    with system_context(reason="test colleagues inactive setup"):
        company = company_model.objects.create(name="Company Active")
    for member in (actor, active, inactive):
        _grant_membership(company, member)

    assert {row["username"] for row in _colleagues(actor)} == {"active-peer"}


@pytest.mark.django_db
def test_service_co_members_are_excluded_from_colleagues() -> None:
    """People pickers list human users only; service rows remain attribution-only."""

    call_command("rebac", "sync", verbosity=0)
    company_model = apps.get_model("iam", "Company")
    actor = User.objects.create_user(username="people-actor", email="people-actor@example.com")
    person = User.objects.create_user(username="person-peer", email="person-peer@example.com")
    service = User.objects.create_user(
        username="service-peer",
        email="service-peer@example.com",
        kind="service",
    )
    with system_context(reason="test colleagues service setup"):
        company = company_model.objects.create(name="Company Service")
    for member in (actor, person, service):
        _grant_membership(company, member)

    assert {row["username"] for row in _colleagues(actor)} == {"person-peer"}


@pytest.mark.django_db
def test_search_filters_colleagues() -> None:
    """``search`` narrows the colleague list by username/name/email substring."""

    call_command("rebac", "sync", verbosity=0)
    company_model = apps.get_model("iam", "Company")
    actor = User.objects.create_user(username="search-actor", email="sa@example.com")
    grace = User.objects.create_user(
        username="grace", email="grace@example.com", first_name="Grace", last_name="Hopper"
    )
    alan = User.objects.create_user(
        username="alan", email="alan@example.com", first_name="Alan", last_name="Turing"
    )
    with system_context(reason="test colleagues search setup"):
        company = company_model.objects.create(name="Company Search")
    for member in (actor, grace, alan):
        _grant_membership(company, member)

    assert {row["username"] for row in _colleagues(actor, search="hopper")} == {"grace"}
    assert {row["username"] for row in _colleagues(actor, search="alan")} == {"alan"}


@pytest.mark.django_db
def test_platform_admin_colleagues_stay_membership_scoped() -> None:
    """Admin reach does not widen ``colleagues`` — it is membership-scoped for everyone.

    A platform admin can read the whole ``users`` catalogue, but ``colleagues`` is a
    people-picker scoped to shared company membership, so an admin who shares no
    company still gets an empty colleague list (the admin catalogue is unaffected).
    """

    call_command("rebac", "sync", verbosity=0)
    company_model = apps.get_model("iam", "Company")
    admin = User.objects.create_superuser(
        username="admin", email="admin@example.com", password="admin"
    )
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    member = User.objects.create_user(username="scoped-member", email="sm@example.com")
    peer = User.objects.create_user(username="scoped-peer", email="sp@example.com")
    with system_context(reason="test colleagues admin setup"):
        company = company_model.objects.create(name="Members Only")
    _grant_membership(company, member)
    _grant_membership(company, peer)

    # The admin shares no company, so colleagues is empty even for an admin …
    assert _colleagues(admin) == []
    # … while the scoped member still sees their co-member.
    assert {row["username"] for row in _colleagues(member)} == {"scoped-peer"}


@pytest.mark.django_db
def test_anonymous_actor_is_denied() -> None:
    """``colleagues`` requires a signed-in actor."""

    call_command("rebac", "sync", verbosity=0)
    result = execute_schema(
        _console_schema(),
        _COLLEAGUES,
        {"search": "", "limit": 20},
        request=graphql_request(AnonymousUser()),
    )

    assert result.errors is not None
