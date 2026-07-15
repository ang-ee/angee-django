"""The REBAC-schema reconcile that keeps schema drift from deadlocking checks.

When an addon leaves ``INSTALLED_APPS``, ``rebac sync`` never revisits it, so its
``Schema*`` rows orphan and the library's ``rebac.E009`` check then blocks every
checked command (``makemigrations``, ``migrate``, ``rebac sync``) — breaking the
rebuild the uninstall triggers. ``platform``'s ``reconcile_permission_schema`` (run
check-free by the ``reconcile_permissions`` command) is the global prune that removes
those orphans and stale rows inside still-composed packages.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command

_OLD_IAM_ZED = """
// @rebac_package: iam
// @rebac_package_version: 0.1.0
// @rebac_schema_revision: 10

definition auth/user {
    relation admin: angee/role // rebac:const=admin

    permission create = admin->member
    permission read = admin->member
    permission write = admin->member
    permission delete = admin->member
}

definition auth/group {
    relation member: auth/user
}

definition angee/role {
    relation member: auth/user | auth/group#member

    permission effective_member = member
}

definition iam/company {
    relation parent:        iam/company // rebac:field=parent
    relation direct_member: auth/user | auth/group#member
    relation admin:         angee/role // rebac:const=admin

    permission member = direct_member + parent->member

    permission create = admin->member
    permission read   = member + admin->member
    permission write  = admin->member
    permission delete = admin->member
}
"""


def _managed(package: str, resource_type: str):
    """Create a SchemaDefinition with a PackageManagedRecord owning it, as sync would."""

    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone
    from rebac.models import PackageManagedRecord, SchemaDefinition

    definition = SchemaDefinition.objects.create(resource_type=resource_type)
    PackageManagedRecord.objects.create(
        package=package,
        external_id=f"definition:{resource_type}",
        schema_revision=1,
        target_ct=ContentType.objects.get_for_model(SchemaDefinition),
        target_pk=definition.pk,
        content_hash="x",
        last_synced_at=timezone.now(),
    )
    return definition


def _managed_relation(package: str, resource_type: str, name: str):
    """Create a SchemaRelation with package-managed provenance."""

    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone
    from rebac.models import PackageManagedRecord, SchemaDefinition, SchemaRelation

    definition = SchemaDefinition.objects.create(resource_type=resource_type)
    relation = SchemaRelation.objects.create(
        definition=definition,
        name=name,
        allowed_subjects=[{"type": "auth/user", "relation": "", "wildcard": False}],
        backing={"attname": "created_by", "kind": "fk"},
    )
    PackageManagedRecord.objects.create(
        package=package,
        external_id=f"definition:{resource_type}",
        schema_revision=1,
        target_ct=ContentType.objects.get_for_model(SchemaDefinition),
        target_pk=definition.pk,
        content_hash="x",
        last_synced_at=timezone.now(),
    )
    PackageManagedRecord.objects.create(
        package=package,
        external_id=f"relation:{resource_type}#{name}",
        schema_revision=1,
        target_ct=ContentType.objects.get_for_model(SchemaRelation),
        target_pk=relation.pk,
        content_hash="x",
        last_synced_at=timezone.now(),
    )
    return definition, relation


def _managed_relation_for_definition(
    package: str,
    definition,
    name: str,
    *,
    backing: dict[str, str] | None,
):
    """Create one package-managed relation on an existing definition."""

    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone
    from rebac.models import PackageManagedRecord, SchemaRelation

    relation = SchemaRelation.objects.create(
        definition=definition,
        name=name,
        allowed_subjects=[{"type": "auth/user", "relation": "", "wildcard": False}],
        backing=backing,
    )
    PackageManagedRecord.objects.create(
        package=package,
        external_id=f"relation:{definition.resource_type}#{name}",
        schema_revision=1,
        target_ct=ContentType.objects.get_for_model(SchemaRelation),
        target_pk=relation.pk,
        content_hash="x",
        last_synced_at=timezone.now(),
    )
    return relation


@pytest.fixture
def _restore_iam_schema_path():
    """Restore IAM's test-time ``rebac_schema`` override after a repoint."""

    from django.apps import apps

    iam = apps.get_app_config("iam")
    sentinel = object()
    original = getattr(iam, "rebac_schema", sentinel)
    yield iam
    if original is sentinel:
        if hasattr(iam, "rebac_schema"):
            delattr(iam, "rebac_schema")
    else:
        iam.rebac_schema = original


@pytest.mark.django_db
def test_reconcile_prunes_old_iam_company_rows_after_schema_removal(
    tmp_path: Path,
    _restore_iam_schema_path,
) -> None:
    """Old ``iam/company`` rows are pruned when IAM's current zed no longer declares them."""

    from rebac import ObjectRef, RelationshipTuple, SubjectRef, write_relationships
    from rebac.models import (
        PackageManagedRecord,
        SchemaDefinition,
        SchemaPermission,
        SchemaRelation,
        active_relationship_model,
    )

    iam = _restore_iam_schema_path
    old_schema = tmp_path / "old_iam_permissions.zed"
    old_schema.write_text(_OLD_IAM_ZED, encoding="utf-8")
    iam.rebac_schema = str(old_schema)
    call_command("rebac", "sync", verbosity=0)

    company = SchemaDefinition.objects.get(resource_type="iam/company")
    assert company.relations.filter(name="direct_member").exists()
    assert company.permissions.filter(name="member").exists()
    assert PackageManagedRecord.objects.filter(package=iam.name, external_id="definition:iam/company").exists()
    write_relationships(
        [
            RelationshipTuple(
                resource=ObjectRef("iam/company", "old-company"),
                relation="direct_member",
                subject=SubjectRef.of("auth/user", "old-member"),
            )
        ]
    )
    relationship_model = active_relationship_model()
    old_direct_member = relationship_model.objects.filter(
        resource_type="iam/company",
        resource_id="old-company",
        relation="direct_member",
        subject_type="auth/user",
        subject_id="old-member",
    )
    assert old_direct_member.exists()

    delattr(iam, "rebac_schema")
    call_command("reconcile_permissions", verbosity=0)

    assert not SchemaDefinition.objects.filter(resource_type="iam/company").exists()
    assert not SchemaRelation.objects.filter(definition=company).exists()
    assert not SchemaPermission.objects.filter(definition=company).exists()
    assert not PackageManagedRecord.objects.filter(
        package=iam.name,
        external_id__in=(
            "definition:iam/company",
            "relation:iam/company#parent",
            "relation:iam/company#direct_member",
            "relation:iam/company#admin",
            "permission:iam/company#member",
            "permission:iam/company#create",
            "permission:iam/company#read",
            "permission:iam/company#write",
            "permission:iam/company#delete",
        ),
    ).exists()
    assert not old_direct_member.exists()
    assert SchemaDefinition.objects.filter(resource_type="auth/user").exists()


def test_reconcile_prunes_orphaned_package_and_keeps_composed(db) -> None:
    """A managed row whose package is not a composed app is pruned with its target,
    while a row for a composed app survives untouched."""

    from django.apps import apps
    from rebac.models import PackageManagedRecord, SchemaDefinition

    from angee.platform.permissions import reconcile_permission_schema

    orphan = _managed("ghost.addon", "ghost/thing")  # no such app in the composed set
    kept_package = apps.get_app_config("contenttypes").name  # a composed app
    kept = _managed(kept_package, "ghost/kept")

    assert reconcile_permission_schema() == 1

    assert not SchemaDefinition.objects.filter(pk=orphan.pk).exists()
    assert not PackageManagedRecord.objects.filter(package="ghost.addon").exists()
    assert SchemaDefinition.objects.filter(pk=kept.pk).exists()
    assert PackageManagedRecord.objects.filter(package=kept_package).exists()


def test_reconcile_prunes_stale_rows_inside_composed_package(db) -> None:
    """A removed definition in a still-installed addon is pruned before checks run."""

    from django.apps import apps
    from rebac.models import PackageManagedRecord, SchemaDefinition, SchemaRelation

    from angee.platform.permissions import reconcile_permission_schema

    package = apps.get_app_config("messaging").name
    stale_definition, stale_relation = _managed_relation(
        package,
        "messaging/message_metrics",
        "owner",
    )
    kept = _managed(package, "messaging/message")

    assert reconcile_permission_schema() == 2

    assert not SchemaRelation.objects.filter(pk=stale_relation.pk).exists()
    assert not SchemaDefinition.objects.filter(pk=stale_definition.pk).exists()
    assert not PackageManagedRecord.objects.filter(
        package=package,
        external_id__in=(
            "definition:messaging/message_metrics",
            "relation:messaging/message_metrics#owner",
        ),
    ).exists()
    assert SchemaDefinition.objects.filter(pk=kept.pk).exists()
    assert PackageManagedRecord.objects.filter(
        package=package,
        external_id="definition:messaging/message",
    ).exists()


@pytest.mark.parametrize("storage_mode", ("denormalized", "registry"))
def test_reconcile_directly_purges_stale_relations_from_active_store(
    db,
    settings,
    storage_mode: str,
) -> None:
    """A renamed field-backed relation cannot block direct stale-tuple cleanup."""

    from django.apps import apps
    from rebac.models import (
        PackageManagedRecord,
        SchemaDefinition,
        SchemaRelation,
        active_relationship_model,
    )

    from angee.platform.permissions import reconcile_permission_schema

    settings.REBAC_LOCAL_BACKEND_STORAGE = storage_mode
    package = apps.get_app_config("nexus").name
    definition = _managed(package, "nexus/tie")
    stale_backed = _managed_relation_for_definition(
        package,
        definition,
        "party",
        backing={"attname": "party", "kind": "fk"},
    )
    stale_stored = _managed_relation_for_definition(
        package,
        definition,
        "legacy_reader",
        backing=None,
    )
    kept = _managed_relation_for_definition(
        package,
        definition,
        "party_a",
        backing={"attname": "party_a", "kind": "fk"},
    )

    relationship_model = active_relationship_model()
    stale_tuple = relationship_model.objects.create(
        resource_type="nexus/tie",
        resource_id="old-tie",
        relation="legacy_reader",
        subject_type="auth/user",
        subject_id="old-reader",
    )
    unrelated_tuple = relationship_model.objects.create(
        resource_type="angee/role",
        resource_id="admin",
        relation="member",
        subject_type="auth/user",
        subject_id="kept-reader",
    )

    assert reconcile_permission_schema() == 2

    assert SchemaDefinition.objects.filter(pk=definition.pk).exists()
    assert SchemaRelation.objects.filter(pk=kept.pk).exists()
    assert not SchemaRelation.objects.filter(pk__in=(stale_backed.pk, stale_stored.pk)).exists()
    assert not PackageManagedRecord.objects.filter(
        package=package,
        external_id__in=(
            "relation:nexus/tie#party",
            "relation:nexus/tie#legacy_reader",
        ),
    ).exists()
    assert not relationship_model.objects.filter(pk=stale_tuple.pk).exists()
    assert relationship_model.objects.filter(pk=unrelated_tuple.pk).exists()


def test_reconcile_is_a_noop_when_nothing_stale(db) -> None:
    """Every managed package composed (here: none managed at all) prunes nothing."""

    from angee.platform.permissions import reconcile_permission_schema

    assert reconcile_permission_schema() == 0
