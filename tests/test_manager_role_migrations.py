"""Stored manager-role subject migration coverage."""

from __future__ import annotations

from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.migrations.state import ProjectState
from django.utils import timezone
from rebac.models import RebacResource, Relationship, RelationshipRegistry

CASES = (
    ("money", ("currency", "currencyrate"), "money/currency", "money/role", "money_admin"),
    ("sequence", ("sequence",), "sequence/sequence", "sequence/role", "sequence_admin"),
    ("uom", ("uomcategory", "uom"), "uom/category", "uom/role", "uom_admin"),
)


def _module(addon: str):
    return import_module(f"angee.{addon}.runtime_migrations.role_subject_relations")


@pytest.mark.parametrize(("addon", "model_names", "resource_type", "role_type", "role_id"), CASES)
def test_manager_role_migration_requires_complete_owner_state(
    addon: str,
    model_names: tuple[str, ...],
    resource_type: str,
    role_type: str,
    role_id: str,
) -> None:
    """Each declaration skips an absent addon and rejects a partial owner."""

    state = ProjectState.from_apps(apps)
    module = _module(addon)
    assert module.applies(state)

    absent = state.clone()
    for model_name in model_names:
        absent.remove_model(addon, model_name)
    assert not module.applies(absent)

    if len(model_names) > 1:
        partial = state.clone()
        partial.remove_model(addon, model_names[0])
        with pytest.raises(ImproperlyConfigured, match=f"angee.{addon}:role_subject_relations"):
            module.applies(partial)

    bad_relationship = state.clone()
    bad_relationship.models[("rebac", "relationship")].fields.pop("subject_type")
    with pytest.raises(ImproperlyConfigured, match="unexpected REBAC state"):
        module.applies(bad_relationship)

    bad_registry = state.clone()
    bad_registry.models[("rebac", "relationshipregistry")].fields.pop("resource_fk")
    with pytest.raises(ImproperlyConfigured, match="unexpected REBAC state"):
        module.applies(bad_registry)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("addon", "model_names", "resource_type", "role_type", "role_id"), CASES)
def test_manager_role_migration_preserves_conditions_in_both_stores(
    addon: str,
    model_names: tuple[str, ...],
    resource_type: str,
    role_type: str,
    role_id: str,
) -> None:
    """Only the obsolete permission suffix changes in either physical store."""

    expires_at = timezone.now() + timedelta(days=1)
    context = {"tenant": addon}
    denormalized = Relationship.objects.create(
        resource_type=resource_type,
        resource_id="owned-row",
        relation="manager",
        subject_type=role_type,
        subject_id=role_id,
        optional_subject_relation="effective_member",
        caveat_name="during_window",
        caveat_context=context,
        expires_at=expires_at,
        written_at_xid=17,
    )
    resource = RebacResource.objects.create(resource_type=resource_type, resource_id="registry-row")
    subject, _ = RebacResource.objects.get_or_create(resource_type=role_type, resource_id=role_id)
    registry = RelationshipRegistry.objects.create(
        resource_fk=resource,
        relation="manager",
        subject_fk=subject,
        optional_subject_relation="effective_member",
        caveat_name="during_window",
        caveat_context=context,
        expires_at=expires_at,
        written_at_xid=17,
    )
    untouched = Relationship.objects.create(
        resource_type="other/resource",
        resource_id="unowned-row",
        relation="manager",
        subject_type=role_type,
        subject_id=role_id,
        optional_subject_relation="effective_member",
    )

    historical_apps = ProjectState.from_apps(apps).apps
    _module(addon).rewrite_manager_subjects(historical_apps, SimpleNamespace(connection=connection))

    denormalized.refresh_from_db()
    registry.refresh_from_db()
    untouched.refresh_from_db()
    for row in (denormalized, registry):
        assert row.optional_subject_relation == ""
        assert row.caveat_name == "during_window"
        assert row.caveat_context == context
        assert row.expires_at == expires_at
        assert row.written_at_xid == 17
    assert untouched.optional_subject_relation == "effective_member"

    _module(addon).rewrite_manager_subjects(historical_apps, SimpleNamespace(connection=connection))
    assert Relationship.objects.filter(pk=denormalized.pk).count() == 1
    assert RelationshipRegistry.objects.filter(pk=registry.pk).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("addon", "model_names", "resource_type", "role_type", "role_id"), CASES)
@pytest.mark.parametrize(("source_xid", "destination_xid"), [(23, 29), (37, 29)])
@pytest.mark.parametrize("registry", [False, True])
def test_manager_role_migration_deduplicates_identical_destinations(
    addon: str,
    model_names: tuple[str, ...],
    resource_type: str,
    role_type: str,
    role_id: str,
    source_xid: int,
    destination_xid: int,
    registry: bool,
) -> None:
    """Matching conditions retain the plain destination and latest causal stamp."""

    common = dict(
        resource_type=resource_type,
        resource_id="duplicate-row",
        relation="manager",
        subject_type=role_type,
        subject_id=role_id,
        caveat_name="condition",
    )
    model = RelationshipRegistry if registry else Relationship
    if registry:
        resource = RebacResource.objects.create(resource_type=resource_type, resource_id="duplicate-row")
        subject, _ = RebacResource.objects.get_or_create(resource_type=role_type, resource_id=role_id)
        for name in ("resource_type", "resource_id", "subject_type", "subject_id"):
            common.pop(name)
        common.update(resource_fk=resource, subject_fk=subject)
    source = model.objects.create(
        **common,
        optional_subject_relation="effective_member",
        caveat_context={
            "same": {"enabled": True, "regions": ["eu", "us"]},
            "scope": "all",
        },
        written_at_xid=source_xid,
    )
    destination = model.objects.create(
        **common,
        optional_subject_relation="",
        caveat_context={
            "scope": "all",
            "same": {"regions": ["eu", "us"], "enabled": True},
        },
        written_at_xid=destination_xid,
    )

    _module(addon).rewrite_manager_subjects(ProjectState.from_apps(apps).apps, SimpleNamespace(connection=connection))

    assert not model.objects.filter(pk=source.pk).exists()
    destination.refresh_from_db()
    assert destination.optional_subject_relation == ""
    assert destination.written_at_xid == max(source_xid, destination_xid)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("addon", "model_names", "resource_type", "role_type", "role_id"), CASES)
def test_manager_role_migration_preflights_all_stores_before_conflict(
    addon: str,
    model_names: tuple[str, ...],
    resource_type: str,
    role_type: str,
    role_id: str,
) -> None:
    """A conflicting registry destination prevents earlier denormalized updates."""

    legacy = Relationship.objects.create(
        resource_type=resource_type,
        resource_id="pending-row",
        relation="manager",
        subject_type=role_type,
        subject_id=role_id,
        optional_subject_relation="effective_member",
    )
    resource = RebacResource.objects.create(resource_type=resource_type, resource_id="conflict-row")
    subject, _ = RebacResource.objects.get_or_create(resource_type=role_type, resource_id=role_id)
    common = dict(
        resource_fk=resource,
        relation="manager",
        subject_fk=subject,
        caveat_name="condition",
    )
    RelationshipRegistry.objects.create(
        **common,
        optional_subject_relation="effective_member",
        caveat_context={"version": {"limits": [1, {"enabled": True}]}},
    )
    RelationshipRegistry.objects.create(
        **common,
        optional_subject_relation="",
        caveat_context={"version": {"limits": [1, {"enabled": 1}]}},
    )

    with pytest.raises(ImproperlyConfigured, match="conflicting manager tuples"):
        _module(addon).rewrite_manager_subjects(
            ProjectState.from_apps(apps).apps, SimpleNamespace(connection=connection)
        )

    legacy.refresh_from_db()
    assert legacy.optional_subject_relation == "effective_member"
