"""Historical role subject-relation migration coverage."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.migrations.state import ProjectState
from django.utils import timezone

from angee.iam.runtime_migrations.role_subject_relations import (
    applies,
    rewrite_role_subject_relations,
)


def test_role_subject_relation_guard_requires_iam_and_coherent_rebac_state() -> None:
    """The data migration waits for IAM and rejects partial tuple storage state."""

    state = ProjectState.from_apps(apps)
    assert applies(state)

    without_iam = state.clone()
    without_iam.remove_model("iam", "user")
    assert not applies(without_iam)

    without_rebac = state.clone()
    without_rebac.remove_model("rebac", "relationship")
    without_rebac.remove_model("rebac", "relationshipregistry")
    assert not applies(without_rebac)

    partial = state.clone()
    partial.remove_model("rebac", "relationshipregistry")
    with pytest.raises(ImproperlyConfigured, match="both REBAC relationship stores"):
        applies(partial)

    unexpected = state.clone()
    unexpected.models[("rebac", "relationship")].fields.pop("expires_at")
    with pytest.raises(ImproperlyConfigured, match="unexpected REBAC relationship state"):
        applies(unexpected)


@pytest.mark.django_db(transaction=True)
def test_role_subject_relations_preserve_tuple_fields_in_both_historical_stores() -> None:
    """Only the obsolete userset suffix changes; conditions and other tuples survive."""

    historical_apps = ProjectState.from_apps(apps).apps
    relationship = historical_apps.get_model("rebac", "Relationship")
    expires_at = timezone.now() + timedelta(days=3)
    denormalized = relationship._base_manager.create(
        resource_type="money/role",
        resource_id="money_admin",
        relation="includes",
        subject_type="money/role",
        subject_id="money_reader",
        optional_subject_relation="effective_member",
        caveat_name="during_close",
        caveat_context={"region": "eu"},
        expires_at=expires_at,
        written_at_xid=41,
    )
    registry_row = _create_registry_relationship(
        historical_apps,
        resource_type="storage/role",
        resource_id="storage_admin",
        subject_type="storage/role",
        subject_id="storage_reader",
        caveat_name="during_close",
        caveat_context={"region": "us"},
        expires_at=expires_at,
        written_at_xid=42,
    )
    nonmatching = relationship._base_manager.create(
        resource_type="money/role",
        resource_id="money_admin",
        relation="includes",
        subject_type="auth/group",
        subject_id="reviewers",
        optional_subject_relation="effective_member",
        caveat_name="",
    )

    _rewrite(historical_apps)
    _rewrite(historical_apps)

    denormalized.refresh_from_db()
    registry_row.refresh_from_db()
    nonmatching.refresh_from_db()
    assert denormalized.optional_subject_relation == ""
    assert denormalized.caveat_name == "during_close"
    assert denormalized.caveat_context == {"region": "eu"}
    assert denormalized.expires_at == expires_at
    assert denormalized.written_at_xid == 41
    assert registry_row.optional_subject_relation == ""
    assert registry_row.caveat_name == "during_close"
    assert registry_row.caveat_context == {"region": "us"}
    assert registry_row.expires_at == expires_at
    assert registry_row.written_at_xid == 42
    assert nonmatching.optional_subject_relation == "effective_member"


@pytest.mark.django_db(transaction=True)
def test_role_subject_relations_deduplicate_identical_destination_conditions() -> None:
    """Destinations carrying the same condition are already the migrated tuples."""

    historical_apps = ProjectState.from_apps(apps).apps
    relationship = historical_apps.get_model("rebac", "Relationship")
    identity = {
        "resource_type": "operator/role",
        "resource_id": "operator_admin",
        "relation": "includes",
        "subject_type": "operator/role",
        "subject_id": "operator_reader",
        "caveat_name": "on_shift",
        "caveat_context": {"shift": "night"},
        "expires_at": timezone.now() + timedelta(days=1),
    }
    relationship._base_manager.create(
        **identity,
        optional_subject_relation="effective_member",
        written_at_xid=30,
    )
    destination = relationship._base_manager.create(
        **identity,
        optional_subject_relation="",
        written_at_xid=20,
    )
    _create_registry_relationship(
        historical_apps,
        resource_type="storage/role",
        resource_id="storage_admin",
        subject_type="storage/role",
        subject_id="storage_reader",
        caveat_name="on_shift",
        caveat_context={
            "shift": "night",
            "rules": {"enabled": True, "regions": ["eu", "us"]},
        },
    )
    registry_destination = _create_registry_relationship(
        historical_apps,
        resource_type="storage/role",
        resource_id="storage_admin",
        subject_type="storage/role",
        subject_id="storage_reader",
        optional_subject_relation="",
        caveat_name="on_shift",
        caveat_context={
            "rules": {"regions": ["eu", "us"], "enabled": True},
            "shift": "night",
        },
    )

    _rewrite(historical_apps)

    rows = relationship._base_manager.filter(
        resource_type="operator/role",
        resource_id="operator_admin",
        relation="includes",
        subject_type="operator/role",
        subject_id="operator_reader",
        caveat_name="on_shift",
    )
    assert list(rows.values_list("pk", "optional_subject_relation")) == [(destination.pk, "")]
    destination.refresh_from_db()
    assert destination.written_at_xid == 30
    registry = historical_apps.get_model("rebac", "RelationshipRegistry")
    registry_rows = registry._base_manager.filter(
        resource_fk__resource_type="storage/role",
        resource_fk__resource_id="storage_admin",
        relation="includes",
        subject_fk__resource_type="storage/role",
        subject_fk__resource_id="storage_reader",
        caveat_name="on_shift",
    )
    assert list(registry_rows.values_list("pk", "optional_subject_relation")) == [
        (registry_destination.pk, "")
    ]


@pytest.mark.django_db(transaction=True)
def test_role_subject_relation_collision_rejects_before_either_store_changes() -> None:
    """Different conditions block the complete two-store rewrite without partial writes."""

    historical_apps = ProjectState.from_apps(apps).apps
    relationship = historical_apps.get_model("rebac", "Relationship")
    source = relationship._base_manager.create(
        resource_type="portfolio/role",
        resource_id="portfolio_admin",
        relation="includes",
        subject_type="portfolio/role",
        subject_id="portfolio_reader",
        optional_subject_relation="effective_member",
        caveat_name="",
    )
    _create_registry_relationship(
        historical_apps,
        resource_type="tags/role",
        resource_id="tags_admin",
        subject_type="tags/role",
        subject_id="tags_reader",
        caveat_name="limited",
        caveat_context={"team": {"limits": [1, {"enabled": True}]}},
    )
    _create_registry_relationship(
        historical_apps,
        resource_type="tags/role",
        resource_id="tags_admin",
        subject_type="tags/role",
        subject_id="tags_reader",
        optional_subject_relation="",
        caveat_name="limited",
        caveat_context={"team": {"limits": [1, {"enabled": 1}]}},
    )

    with pytest.raises(ImproperlyConfigured, match="different caveat context or expiry"):
        _rewrite(historical_apps)

    source.refresh_from_db()
    assert source.optional_subject_relation == "effective_member"


def _rewrite(historical_apps: Any) -> None:
    rewrite_role_subject_relations(
        historical_apps,
        SimpleNamespace(connection=connection),
    )


def _create_registry_relationship(
    historical_apps: Any,
    *,
    resource_type: str,
    resource_id: str,
    subject_type: str,
    subject_id: str,
    optional_subject_relation: str = "effective_member",
    caveat_name: str,
    caveat_context: dict[str, Any] | None = None,
    expires_at: Any = None,
    written_at_xid: int = 0,
) -> Any:
    resource_model = historical_apps.get_model("rebac", "RebacResource")
    relationship = historical_apps.get_model("rebac", "RelationshipRegistry")
    resource, _ = resource_model._base_manager.get_or_create(
        resource_type=resource_type,
        resource_id=resource_id,
    )
    subject, _ = resource_model._base_manager.get_or_create(
        resource_type=subject_type,
        resource_id=subject_id,
    )
    return relationship._base_manager.create(
        resource_fk=resource,
        relation="includes",
        subject_fk=subject,
        optional_subject_relation=optional_subject_relation,
        caveat_name=caveat_name,
        caveat_context=caveat_context,
        expires_at=expires_at,
        written_at_xid=written_at_xid,
    )
