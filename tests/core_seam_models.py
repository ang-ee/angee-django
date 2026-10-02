"""Small extensions of the shared compositions for base seam regressions."""

from dataclasses import replace

import pytest
from django.db import models
from rebac import to_subject_ref
from rebac.backends import backend, reset_backend
from rebac.schema import parse_zed

from angee.base.errors import RecordAccessSubjectRefused
from angee.base.stages import Stage
from angee.testing.permissions import install_permission_schema
from tests.conftest import Vault
from tests.core_persistence import OwnedRow


class SubjectGuardedRow(OwnedRow):
    """A declared holder invariant on a child of the shared ownership probe."""

    rebac_grantable = {"reader": "share", "editor": "share"}
    owner_transfer_permission = "child_transfer"
    child_id = models.IntegerField(primary_key=True)
    refused_subject = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/subject_guarded_row"

    def validate_record_access_subject(self, relation, subject):
        super().validate_record_access_subject(relation, subject)
        if str(to_subject_ref(subject)) == self.refused_subject:
            raise RecordAccessSubjectRefused()


class SubjectGuardedLeaf(SubjectGuardedRow):
    """Two concrete ancestors, each with a different primary key."""

    leaf_id = models.IntegerField(primary_key=True)

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/subject_guarded_leaf"


class ConstrainedVaultChild(Vault):
    """Reuse the real owner/name constraint rather than copying its definition."""

    child_id = models.IntegerField(primary_key=True)

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/constrained_vault_child"


class FallbackContainer(models.Model):
    default_stage = models.ForeignKey("scopedemo.FallbackStage", null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "scopedemo"


class FallbackStage(Stage):
    container_field_name = "container"
    container = models.ForeignKey(FallbackContainer, on_delete=models.CASCADE)

    class Meta(Stage.Meta):
        abstract = False
        app_label = "scopedemo"


SUBJECT_SCHEMA = """
definition auth/group { relation member: auth/user }
definition scopedemo/subject_guarded_row {
    relation parent: scopedemo/owned_row // rebac:field=ownedrow_ptr
    relation reader: auth/user | auth/user:* | auth/group#member
    relation editor: auth/user | auth/user:* | auth/group#member
    permission read = parent->read + reader + editor
    permission create = authenticated
    permission write = parent->write
    permission delete = parent->delete
    permission share = parent->transfer
    permission child_transfer = nil
    permission write__owner = parent->transfer
}
definition scopedemo/subject_guarded_leaf {
    relation parent: scopedemo/subject_guarded_row // rebac:field=subjectguardedrow_ptr
    relation owner_row: scopedemo/owned_row // rebac:field=subjectguardedrow_ptr__ownedrow_ptr
    permission read = parent->read
    permission create = authenticated
    permission write = parent->write
    permission delete = parent->delete
    permission child_transfer = authenticated
    permission write__owner = owner_row->transfer
}
"""


@pytest.fixture
def subject_tables(ownership_tables):
    active = backend()
    install_permission_schema(replace(active.schema(), definitions=[
        *active.schema().definitions, *parse_zed(SUBJECT_SCHEMA).definitions,
    ]), active=active)


@pytest.fixture
def constrained_vault_tables(composed_tables):
    active = backend()
    extra = parse_zed("""
        definition scopedemo/constrained_vault_child {
            relation parent: knowledge/vault // rebac:field=vault_ptr
            permission read = parent->read
            permission create = authenticated
            permission write = parent->write
            permission delete = parent->delete
            permission write__owner = parent->transfer
        }
    """)
    install_permission_schema(
        replace(active.schema(), definitions=[*active.schema().definitions, *extra.definitions]),
        active=active,
    )
    try:
        yield
    finally:
        reset_backend()
