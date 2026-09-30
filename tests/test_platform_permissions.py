"""The REBAC-schema reconcile that keeps schema drift from deadlocking checks.

When an addon leaves ``INSTALLED_APPS``, ``rebac sync`` never revisits it, so its
``Schema*`` rows orphan and the library's ``rebac.E009`` check then blocks every
checked command (``makemigrations``, ``migrate``, ``rebac sync``) — breaking the
rebuild the uninstall triggers. ``platform``'s ``reconcile_permission_schema`` (run
check-free by the ``reconcile_permissions`` command) is the global prune that removes
those orphans and stale rows inside still-composed packages.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.utils import timezone
from rebac import ObjectRef, RelationshipTuple, SubjectRef
from rebac.models import (
    PackageManagedRecord,
    SchemaDefinition,
    SchemaPermission,
    SchemaRelation,
    active_relationship_model,
)
from rebac.schema import parse_zed
from rebac.schema.ast import FieldBinding, backing_to_dict

from angee.base.historical_relationships import ensure_historical_relationships
from angee.platform.permissions import reconcile_permission_schema

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


def _provenance(historical_models, package: str, external_id: str, target) -> None:
    """Retain the package ownership present before index publication existed."""
    historical_models.get_model("rebac", "PackageManagedRecord").objects.create(
        package=package,
        external_id=external_id,
        schema_revision=1,
        target_ct_id=ContentType.objects.get_for_model(target).pk,
        target_pk=target.pk,
        content_hash="x",
        last_synced_at=timezone.now(),
    )


def _managed(historical_models, package: str, resource_type: str):
    """Create a SchemaDefinition with a PackageManagedRecord owning it, as sync would."""

    definition = historical_models.get_model("rebac", "SchemaDefinition").objects.create(resource_type=resource_type)
    _provenance(historical_models, package, f"definition:{resource_type}", definition)
    return definition


def _managed_relation(historical_models, package: str, resource_type: str, name: str):
    """Create a SchemaRelation with package-managed provenance."""

    definition = _managed(historical_models, package, resource_type)
    relation = _managed_relation_for_definition(
        historical_models, package, definition, name, backing=backing_to_dict(FieldBinding("created_by")),
    )
    return definition, relation


def _managed_relation_for_definition(
    historical_models,
    package: str,
    definition,
    name: str,
    *,
    backing: dict[str, str] | None,
):
    """Create one package-managed relation on an existing definition."""

    relation = historical_models.get_model("rebac", "SchemaRelation").objects.create(
        definition=definition,
        name=name,
        allowed_subjects=[{"type": "auth/user", "relation": "", "wildcard": False}],
        backing=backing,
    )
    _provenance(historical_models, package, f"relation:{definition.resource_type}#{name}", relation)
    return relation


@pytest.mark.django_db
def test_reconcile_prunes_old_iam_company_rows_after_schema_removal(
    historical_rebac_models,
) -> None:
    """Old ``iam/company`` rows are pruned when IAM's current zed no longer declares them."""

    iam = apps.get_app_config("iam")
    for declaration in parse_zed(_OLD_IAM_ZED).definitions:
        definition = _managed(historical_rebac_models, iam.name, declaration.resource_type)
        for item in declaration.relations:
            relation = historical_rebac_models.get_model("rebac", "SchemaRelation").objects.create(
                definition=definition, name=item.name,
                allowed_subjects=[asdict(subject) for subject in item.allowed_subjects],
                backing=backing_to_dict(item.backing), with_expiration=item.with_expiration,
            )
            _provenance(
                historical_rebac_models, iam.name, f"relation:{declaration.resource_type}#{item.name}", relation,
            )
        for item in declaration.permissions:
            permission = historical_rebac_models.get_model("rebac", "SchemaPermission").objects.create(
                definition=definition, name=item.name, expression=item.raw_text,
            )
            _provenance(
                historical_rebac_models, iam.name, f"permission:{declaration.resource_type}#{item.name}", permission,
            )

    company = SchemaDefinition.objects.get(resource_type="iam/company")
    assert company.relations.filter(name="direct_member").exists()
    assert company.permissions.filter(name="member").exists()
    assert PackageManagedRecord.objects.filter(package=iam.name, external_id="definition:iam/company").exists()
    ensure_historical_relationships(
        historical_rebac_models, using="default", relationships=[
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


def test_reconcile_prunes_orphaned_package_and_keeps_composed(db, historical_rebac_models) -> None:
    """A managed row whose package is not a composed app is pruned with its target,
    while a row for a composed app survives untouched."""

    orphan = _managed(historical_rebac_models, "ghost.addon", "ghost/thing")  # no such app in the composed set
    kept_package = apps.get_app_config("contenttypes").name  # a composed app
    kept = _managed(historical_rebac_models, kept_package, "ghost/kept")

    assert reconcile_permission_schema() == 1

    assert not SchemaDefinition.objects.filter(pk=orphan.pk).exists()
    assert not PackageManagedRecord.objects.filter(package="ghost.addon").exists()
    assert SchemaDefinition.objects.filter(pk=kept.pk).exists()
    assert PackageManagedRecord.objects.filter(package=kept_package).exists()


def test_reconcile_prunes_stale_rows_inside_composed_package(db, historical_rebac_models) -> None:
    """A removed definition in a still-installed addon is pruned before checks run."""

    package = apps.get_app_config("messaging").name
    stale_definition, stale_relation = _managed_relation(
        historical_rebac_models,
        package,
        "messaging/message_metrics",
        "owner",
    )
    kept = _managed(historical_rebac_models, package, "messaging/message")

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
    historical_rebac_models,
    storage_mode: str,
) -> None:
    """A renamed field-backed relation cannot block direct stale-tuple cleanup."""

    settings.REBAC_LOCAL_BACKEND_STORAGE = storage_mode
    package = apps.get_app_config("knowledge").name
    definition = _managed(historical_rebac_models, package, "knowledge/page")
    stale_backed = _managed_relation_for_definition(
        historical_rebac_models,
        package,
        definition,
        "author",
        backing=backing_to_dict(FieldBinding("author")),
    )
    stale_stored = _managed_relation_for_definition(
        historical_rebac_models,
        package,
        definition,
        "legacy_reader",
        backing=None,
    )
    kept = _managed_relation_for_definition(
        historical_rebac_models,
        package,
        definition,
        "owner",
        backing=backing_to_dict(FieldBinding("created_by")),
    )

    relationship_model = active_relationship_model()
    ensure_historical_relationships(
        historical_rebac_models, using="default", relationships=[
            RelationshipTuple(
                resource=ObjectRef("knowledge/page", "901"), relation="legacy_reader",
                subject=SubjectRef.of("auth/user", "902"),
            ),
            RelationshipTuple(
                resource=ObjectRef("knowledge/role", "vault_viewer"), relation="member",
                subject=SubjectRef.of("auth/user", "903"),
            ),
        ],
    )
    stale_tuple = relationship_model.objects.get(resource_type="knowledge/page", resource_id="901")
    unrelated_tuple = relationship_model.objects.get(resource_type="knowledge/role", resource_id="vault_viewer")

    assert reconcile_permission_schema() == 2

    assert SchemaDefinition.objects.filter(pk=definition.pk).exists()
    assert SchemaRelation.objects.filter(pk=kept.pk).exists()
    assert not SchemaRelation.objects.filter(pk__in=(stale_backed.pk, stale_stored.pk)).exists()
    assert not PackageManagedRecord.objects.filter(
        package=package,
        external_id__in=(
            "relation:knowledge/page#author",
            "relation:knowledge/page#legacy_reader",
        ),
    ).exists()
    assert not relationship_model.objects.filter(pk=stale_tuple.pk).exists()
    assert relationship_model.objects.filter(pk=unrelated_tuple.pk).exists()


def test_reconcile_is_a_noop_when_nothing_stale(db) -> None:
    """Every managed package composed (here: none managed at all) prunes nothing."""

    assert reconcile_permission_schema() == 0
