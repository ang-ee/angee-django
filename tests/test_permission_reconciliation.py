"""Composed policy upgrades dispatch to native REBAC synchronization.

Angee owns effective addon schema composition and provision ordering. Native
REBAC owns stale declaration and stored tuple retirement; these integration
cases exercise it against Angee's real IAM and record-edge declarations.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command
from rebac import schema_changes
from rebac.schema.ast import FieldBinding, backing_to_dict

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


def _managed_relation_for_definition(
    package: str,
    definition,
    name: str,
    *,
    backing: dict[str, str] | None,
    subject_type: str = "auth/user",
):
    """Create one package-managed relation on an existing definition."""

    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone
    from rebac.models import PackageManagedRecord, SchemaRelation

    relation = SchemaRelation.objects.create(
        definition=definition,
        name=name,
        allowed_subjects=[{"type": subject_type, "relation": "", "wildcard": False}],
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
@schema_changes()
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
    call_command("rebac", "sync", "--prune-stale", "--yes", skip_checks=True, verbosity=0)

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


@pytest.mark.parametrize("storage_mode", ("denormalized", "registry"))
@schema_changes()
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


    settings.REBAC_LOCAL_BACKEND_STORAGE = storage_mode
    package = apps.get_app_config("nexus").name
    definition = _managed(package, "nexus/tie")
    stale_backed = _managed_relation_for_definition(
        package,
        definition,
        "party",
        backing=backing_to_dict(FieldBinding(path="party")),
        subject_type="parties/party",
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
        backing=backing_to_dict(FieldBinding(path="party_a")),
        subject_type="parties/party",
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
        resource_type="knowledge/role",
        resource_id="vault_viewer",
        relation="member",
        subject_type="auth/user",
        subject_id="kept-reader",
    )
    stale_userset = relationship_model.objects.create(
        resource_type="knowledge/role",
        resource_id="vault_viewer",
        relation="member",
        subject_type="nexus/tie",
        subject_id="old-tie",
        optional_subject_relation="legacy_reader",
    )
    kept_userset = relationship_model.objects.create(
        resource_type="knowledge/role",
        resource_id="vault_viewer",
        relation="member",
        subject_type="nexus/tie",
        subject_id="old-tie",
        optional_subject_relation="party_a",
    )

    call_command("rebac", "sync", "--prune-stale", "--yes", skip_checks=True, verbosity=0)

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
    assert not relationship_model.objects.filter(pk=stale_userset.pk).exists()
    assert relationship_model.objects.filter(pk=kept_userset.pk).exists()
    assert relationship_model.objects.filter(pk=unrelated_tuple.pk).exists()


def test_permissions_import_before_app_population() -> None:
    """Model declarations can import permission helpers before the registry is populated."""

    result = subprocess.run(
        [sys.executable, "-c", """
from django.apps import apps
from django.conf import settings
settings.configure(
    SECRET_KEY="permission-import-probe",
    INSTALLED_APPS=["django.contrib.contenttypes", "rebac", "angee"],
)
assert not apps.apps_ready
import angee.base.permissions
assert not apps.apps_ready
assert not apps.models_ready
"""],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
