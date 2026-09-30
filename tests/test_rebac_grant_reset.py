"""Explicit destructive REBAC grant reset coverage."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from rebac import ObjectRef, to_subject_ref
from rebac.backends import LocalBackend, reset_backend
from rebac.backends import backend as configured_backend
from rebac.index.rebuild import rebuild
from rebac.models import RebacResource, Relationship, RelationshipRegistry
from rebac.schema import parse_zed

from tests.conftest import create_user


@pytest.mark.django_db(transaction=True)
def test_reset_rebac_grants_previews_then_clears_both_stores(request: pytest.FixtureRequest) -> None:
    """Preview is inert; apply clears tuples and registry identities together."""

    reset_backend()
    request.addfinalizer(reset_backend)
    actor = to_subject_ref(create_user("legacy-grant-reader"))
    Relationship._base_manager.create(
        resource_type="tests/document",
        resource_id="old-public-id",
        relation="reader",
        subject_type="auth/user",
        subject_id=actor.subject_id,
        optional_subject_relation="",
    )
    resource = RebacResource._base_manager.create(
        resource_type="tests/document",
        resource_id="old-public-id",
    )
    subject = RebacResource._base_manager.create(
        resource_type="auth/user",
        resource_id=actor.subject_id,
    )
    RelationshipRegistry._base_manager.create(
        resource_fk=resource,
        relation="reader",
        subject_fk=subject,
        optional_subject_relation="",
    )
    backend = configured_backend()
    assert isinstance(backend, LocalBackend)
    backend.set_schema(
        parse_zed("""
        definition auth/user {}
        definition tests/document {
          relation reader: auth/user
          permission read = reader
        }
    """)
    )
    rebuild(using="default")
    document = ObjectRef("tests/document", "old-public-id")
    assert backend.check_access(subject=actor, action="read", resource=document).allowed

    preview = StringIO()
    call_command("reset_rebac_grants", stdout=preview)
    assert "would discard: 1 denormalized relationships, 1 registry relationships" in preview.getvalue()
    assert Relationship._base_manager.count() == 1
    assert RelationshipRegistry._base_manager.count() == 1
    assert RebacResource._base_manager.count() == 2

    applied = StringIO()
    call_command("reset_rebac_grants", apply=True, stdout=applied)
    assert "discarded: 1 denormalized relationships, 1 registry relationships" in applied.getvalue()
    assert "run 'bootstrap_admin' before resuming traffic" in applied.getvalue()
    assert not Relationship._base_manager.exists()
    assert not RelationshipRegistry._base_manager.exists()
    assert not RebacResource._base_manager.exists()
    assert not backend.check_access(subject=actor, action="read", resource=document).allowed
