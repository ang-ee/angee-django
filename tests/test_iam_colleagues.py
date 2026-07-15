"""Tests for IAM's REBAC-native people directory surface."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from rebac import (
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model

from angee.resources.models import Resource
from tests.conftest import addon_schema, execute_schema, graphql_request, result_data

User = get_user_model()
iam_schema = importlib.import_module("angee.iam.schema")

_EVERYONE = SubjectRef.of("auth/user", "*")
_DIRECTORY = ObjectRef("iam/directory", "main")
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


def _grant_directory_reader(resource: Any, subject: Any = _EVERYONE) -> None:
    """Grant ``subject`` IAM directory-read reach on ``resource``."""

    subject_ref = subject if isinstance(subject, SubjectRef) else to_subject_ref(subject)
    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(resource),
                relation="directory_reader",
                subject=subject_ref,
            )
        ]
    )


def _grant_directory(subject: Any = _EVERYONE) -> None:
    """Grant ``subject`` platform-wide IAM directory reach."""

    subject_ref = subject if isinstance(subject, SubjectRef) else to_subject_ref(subject)
    write_relationships(
        [
            RelationshipTuple(
                resource=_DIRECTORY,
                relation="reader",
                subject=subject_ref,
            )
        ]
    )


@pytest.fixture
def iam_directory_schema(transactional_db: Any) -> None:
    """Load the current REBAC schema before directory grants are written."""

    del transactional_db
    call_command("rebac", "sync", verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_seeded_wildcard_directory_reader_exposes_active_human_directory(iam_directory_schema: None) -> None:
    """The shipped singleton wildcard posture opens user reads for non-admin actors."""

    del iam_directory_schema
    actor = User.objects.create_user(username="actor", email="actor@example.com")
    User.objects.create_user(username="peer", email="peer@example.com")
    User.objects.create_user(
        username="inactive-peer",
        email="inactive@example.com",
        is_active=False,
    )
    User.objects.create_user(
        username="service-peer",
        email="service@example.com",
        kind="service",
    )
    _grant_directory()

    rows = _colleagues(actor)

    assert {row["username"] for row in rows} == {"actor", "peer"}
    assert not active_relationship_model().objects.filter(
        resource_type="auth/user",
        relation="directory_reader",
        subject_type="auth/user",
        subject_id="*",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_absent_wildcard_directory_seed_leaves_only_directly_authorized_rows(iam_directory_schema: None) -> None:
    """Without the wildcard seed, a member sees only rows with direct grants."""

    del iam_directory_schema
    actor = User.objects.create_user(username="actor", email="actor@example.com")
    granted = User.objects.create_user(username="granted", email="granted@example.com")
    hidden = User.objects.create_user(username="hidden", email="hidden@example.com")
    _grant_directory_reader(actor, actor)
    _grant_directory_reader(granted, actor)

    rows = _colleagues(actor)

    assert {row["username"] for row in rows} == {"actor", "granted"}
    assert hidden.username not in {row["username"] for row in rows}
    assert not active_relationship_model().objects.filter(
        resource_type="iam/directory",
        resource_id="main",
        relation="reader",
        subject_type="auth/user",
        subject_id="*",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_search_ordering_and_limit_are_user_collection_mechanics(iam_directory_schema: None) -> None:
    """Search, deterministic ordering, and caps layer over actor-scoped rows."""

    del iam_directory_schema
    actor = User.objects.create_user(username="search-actor", email="sa@example.com")
    User.objects.create_user(
        username="grace",
        email="grace@example.com",
        first_name="Grace",
        last_name="Hopper",
    )
    User.objects.create_user(
        username="alan",
        email="alan@example.com",
        first_name="Alan",
        last_name="Turing",
    )
    _grant_directory()

    assert {row["username"] for row in _colleagues(actor, search="hopper")} == {"grace"}
    assert [row["username"] for row in _colleagues(actor, limit=1)] == ["alan"]


@pytest.mark.django_db(transaction=True)
def test_visible_person_from_public_id_uses_actor_scoped_queryset(iam_directory_schema: None) -> None:
    """Actions resolving a selected person cannot drift from the picker."""

    del iam_directory_schema
    actor = User.objects.create_user(username="actor", email="actor@example.com")
    visible = User.objects.create_user(username="visible", email="visible@example.com")
    hidden = User.objects.create_user(username="hidden", email="hidden@example.com")
    _grant_directory(actor)

    assert User.objects.visible_person_from_public_id(actor, visible.public_id) == visible
    assert User.objects.visible_person_from_public_id(actor, hidden.public_id) == hidden


class IamDemoResourceLedger(Resource):
    """Concrete resource ledger for IAM demo resource-load tests."""

    class Meta(Resource.Meta):
        """Django model options for the test ledger."""

        app_label = "base"
        abstract = False
        managed = False
        db_table = "test_iam_demo_resource"


@pytest.fixture
def iam_demo_resource_ledger(transactional_db: Any) -> None:
    """Create the resource ledger table used by IAM demo load tests."""

    del transactional_db
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(IamDemoResourceLedger)
    try:
        yield
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(IamDemoResourceLedger)


def _load_iam_demo_resources() -> None:
    """Load IAM's real demo resource tier through the resource manager."""

    call_command("rebac", "sync", verbosity=0)
    IamDemoResourceLedger.objects.load_addons(
        (apps.get_app_config("iam"),),
        tiers=[Resource.Tier.DEMO],
        allow_non_dev=True,
    )


@pytest.mark.django_db(transaction=True)
def test_iam_demo_resources_seed_directory_for_non_admin_user(iam_demo_resource_ledger: None) -> None:
    """IAM demo resources seed a tuple-driven directory readable by fixture users."""

    del iam_demo_resource_ledger
    _load_iam_demo_resources()

    with system_context(reason="test.iam.demo-directory.actor"):
        alice = User.objects.get(username="alice")
    rows = _colleagues(alice)

    assert {row["username"] for row in rows} == {"admin", "alice", "bob"}
    assert active_relationship_model().objects.filter(
        resource_type="iam/directory",
        resource_id="main",
        relation="reader",
        subject_type="auth/user",
        subject_id="*",
    ).exists()
    assert not active_relationship_model().objects.filter(
        resource_type="auth/user",
        relation="directory_reader",
        subject_type="auth/user",
        subject_id="*",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_iam_demo_directory_includes_user_created_after_resource_load(iam_demo_resource_ledger: None) -> None:
    """A post-load user is still visible because directory reach is singleton-backed."""

    del iam_demo_resource_ledger
    _load_iam_demo_resources()

    with system_context(reason="test.iam.demo-directory.post-load-user"):
        alice = User.objects.get(username="alice")
    created_later = User.objects.create_user(username="charlie", email="charlie@example.com")

    assert "charlie" in {row["username"] for row in _colleagues(alice)}
    assert {"admin", "alice", "bob", "charlie"} == {row["username"] for row in _colleagues(created_later)}


@pytest.mark.django_db(transaction=True)
def test_anonymous_actor_is_denied(iam_directory_schema: None) -> None:
    """``colleagues`` requires a signed-in actor."""

    del iam_directory_schema
    result = execute_schema(
        _console_schema(),
        _COLLEAGUES,
        {"search": "", "limit": 20},
        request=graphql_request(AnonymousUser()),
    )

    assert result.errors is not None
