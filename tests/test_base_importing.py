"""Import authority, immutable provenance, and persistence guard contracts."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Iterator

import pytest
from django.db import DatabaseError, connection, models, transaction
from rebac import system_context

from angee.base.importing import (
    ExternalOwnershipContribution,
    ExternalOwnershipDeclaration,
    ExternalOwnershipMixin,
    ImportAuthorityError,
    ImportCompany,
    ImportOperation,
    ImportSource,
    ImportTarget,
    current_import_operation,
    import_operation,
)
from angee.base.models import AngeeModel
from angee.base.ownership import (
    install_external_ownership_guard,
    install_external_ownership_relation_guard,
    remove_external_ownership_guard,
    remove_external_ownership_relation_guard,
)


def test_foundation_enforcement_bootstraps_without_integrate() -> None:
    """Authority and SQL callbacks work without any connection infrastructure."""

    script = textwrap.dedent('''
        from django.conf import settings
        settings.configure(
            SECRET_KEY="foundation-test",
            INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "rebac", "angee.base"],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        )
        import django
        django.setup()
        import sys
        from django.apps import apps
        from django.db import connection, transaction
        from angee.base.importing import ImportOperation, ImportSource, ImportTarget, import_operation
        assert not apps.is_installed("angee.integrate")
        assert not any(name.startswith("angee.integrate") for name in sys.modules)
        sql = "SELECT angee_import_scope_allows('fixture', 'one', NULL, NULL, 'test.row', '1', 'load')"
        with connection.cursor() as cursor:
            cursor.execute(sql)
            assert cursor.fetchone() == (0,)
        with transaction.atomic(), import_operation(ImportOperation(
            source=ImportSource("fixture", "one"), company=None,
            operation="load", run="one", targets=(ImportTarget("test.row"),),
        )):
            with connection.cursor() as cursor:
                cursor.execute(sql)
                assert cursor.fetchone() == (1,)
    ''')
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)


class OwnedTag(models.Model):
    """Unowned catalogue endpoint for the guarded many-to-many edge."""

    name = models.CharField(max_length=64)

    class Meta:
        app_label = "tests"
        db_table = "test_integrate_owned_tag"


class OwnedImportRow(ExternalOwnershipMixin, AngeeModel):
    """Small source-owned target exercising the public framework seam."""

    name = models.CharField(max_length=64)
    note = models.CharField(max_length=64, blank=True)
    tags = models.ManyToManyField(OwnedTag)

    external_ownership = ExternalOwnershipDeclaration(
        source_owned_fields={"name", "tags"},
        local_fields={"note"},
        operations={"fixture.import"},
    )

    class Meta:
        app_label = "tests"
        db_table = "test_integrate_owned_import_row"


class OwnedImportChild(OwnedImportRow):
    """MTI child whose provenance and company scope live on its parent table."""

    detail = models.CharField(max_length=64)
    external_ownership = ExternalOwnershipDeclaration(
        source_owned_fields={"name", "tags", "detail"},
        local_fields={"note"},
        operations={"fixture.import"},
    )

    class Meta:
        app_label = "tests"
        db_table = "test_integrate_owned_import_child"


class OwnedImportEdge(ExternalOwnershipMixin, AngeeModel):
    """Edge whose endpoint remains protected even if the edge is unclaimed."""

    endpoint = models.ForeignKey(OwnedImportRow, on_delete=models.CASCADE)
    amount = models.IntegerField()
    note = models.CharField(max_length=64, blank=True)
    external_ownership = ExternalOwnershipDeclaration(
        source_owned_fields={"endpoint", "amount"},
        local_fields={"note"},
        protected_relations={"endpoint"},
        operations={"fixture.import"},
    )

    class Meta:
        app_label = "tests"
        db_table = "test_integrate_owned_import_edge"


class CompanyOwnedImportRow(ExternalOwnershipMixin, AngeeModel):
    name = models.CharField(max_length=64)
    source_company = models.ForeignKey(OwnedTag, null=True, on_delete=models.PROTECT)
    external_ownership = ExternalOwnershipDeclaration(
        source_owned_fields={"name", "source_company"},
        operations={"fixture.import"},
        company_field="source_company",
    )

    class Meta:
        app_label = "tests"
        db_table = "test_integrate_company_owned_import_row"


class LocalOverlayDonation(models.Model):
    """Static extension donor contributing a locally mutable field."""

    review = models.CharField(max_length=64, blank=True)
    external_ownership = ExternalOwnershipContribution(local_fields={"review"})

    class Meta:
        abstract = True


class DonatedOwnedImportRow(LocalOverlayDonation, OwnedImportRow):
    """Runtime-shaped donor composition used to verify additive declarations."""

    class Meta:
        app_label = "tests"
        db_table = "test_integrate_donated_owned_import_row"


@pytest.fixture()
def owned_table() -> Iterator[None]:
    """Create one guarded table without involving generated runtime migrations."""

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(OwnedTag)
        schema_editor.create_model(OwnedImportRow)
        schema_editor.create_model(OwnedImportChild)
        schema_editor.create_model(OwnedImportEdge)
        schema_editor.create_model(CompanyOwnedImportRow)
        schema_editor.create_model(DonatedOwnedImportRow)
        install_external_ownership_guard(schema_editor, OwnedImportRow)
        install_external_ownership_relation_guard(schema_editor, OwnedImportRow, "tags")
        install_external_ownership_guard(schema_editor, OwnedImportChild)
        install_external_ownership_guard(schema_editor, OwnedImportEdge)
        install_external_ownership_guard(schema_editor, CompanyOwnedImportRow)
        install_external_ownership_guard(schema_editor, DonatedOwnedImportRow)
    try:
        yield
    finally:
        with connection.schema_editor() as schema_editor:
            remove_external_ownership_relation_guard(schema_editor, OwnedImportRow, "tags")
            remove_external_ownership_guard(schema_editor, DonatedOwnedImportRow)
            remove_external_ownership_guard(schema_editor, OwnedImportChild)
            remove_external_ownership_guard(schema_editor, OwnedImportEdge)
            remove_external_ownership_guard(schema_editor, CompanyOwnedImportRow)
            remove_external_ownership_guard(schema_editor, OwnedImportRow)
            schema_editor.delete_model(DonatedOwnedImportRow)
            schema_editor.delete_model(OwnedImportEdge)
            schema_editor.delete_model(CompanyOwnedImportRow)
            schema_editor.delete_model(OwnedImportChild)
            schema_editor.delete_model(OwnedImportRow)
            schema_editor.delete_model(OwnedTag)


def _operation(*, source_id: str = "source-1") -> ImportOperation:
    return ImportOperation(
        source=ImportSource("integrate.fixture", source_id),
        company=None,
        operation="fixture.import",
        run="run-1",
        targets=(ImportTarget("tests.ownedimportrow"),),
    )


@pytest.mark.django_db(transaction=True)
def test_first_import_and_source_owned_changes_require_exact_typed_authority(owned_table: None) -> None:
    """Protection exists before a first row and reason text grants no authority."""

    row = OwnedImportRow(
        name="source name",
        external_source_type="integrate.fixture",
        external_source_id="source-1",
        external_source_key="remote-7",
    )
    with system_context(reason="fixture.import"), pytest.raises(ImportAuthorityError):
        row.save()

    with transaction.atomic(), import_operation(_operation()):
        row.save()

    row.name = "forged"
    with system_context(reason="fixture.import"), pytest.raises(ImportAuthorityError):
        row.save(update_fields=["name"])

    with transaction.atomic(), import_operation(_operation(source_id="other")), pytest.raises(ImportAuthorityError):
        row.save(update_fields=["name"])

    row.name = "refreshed"
    with transaction.atomic(), import_operation(_operation()):
        row.save(update_fields=["name"])
    assert OwnedImportRow.objects.sudo(reason="test.verify").get(pk=row.pk).name == "refreshed"


@pytest.mark.django_db(transaction=True)
def test_nullable_company_scope_uses_null_company_type(owned_table: None) -> None:
    row = CompanyOwnedImportRow(
        name="shared",
        external_source_type="integrate.fixture",
        external_source_id="source-1",
        external_source_key="shared-1",
    )
    operation = ImportOperation(
        ImportSource("integrate.fixture", "source-1"),
        None,
        "fixture.import",
        "run-shared",
        (ImportTarget("tests.companyownedimportrow"),),
    )
    with transaction.atomic(), import_operation(operation):
        row.save()
    wrong = ImportOperation(
        operation.source,
        ImportCompany("tests.ownedtag", "999"),
        operation.operation,
        "run-wrong",
        operation.targets,
    )
    row.name = "wrong"
    with transaction.atomic(), import_operation(wrong), pytest.raises(ImportAuthorityError):
        row.save(update_fields=["name"])


@pytest.mark.django_db(transaction=True)
def test_source_owned_company_is_immutable_in_orm_and_raw_sql(owned_table: None) -> None:
    company_a = OwnedTag.objects.create(name="Company A")
    company_b = OwnedTag.objects.create(name="Company B")

    def operation(company: OwnedTag) -> ImportOperation:
        return ImportOperation(
            ImportSource("integrate.fixture", "source-1"),
            ImportCompany("tests.ownedtag", str(company.pk)),
            "fixture.import",
            f"run-{company.pk}",
            (ImportTarget("tests.companyownedimportrow"),),
        )

    row = CompanyOwnedImportRow(
        name="owned by A",
        source_company=company_a,
        external_source_type="integrate.fixture",
        external_source_id="source-1",
        external_source_key="company-row",
    )
    with transaction.atomic(), import_operation(operation(company_a)):
        row.save()

    row.source_company = company_b
    with transaction.atomic(), import_operation(operation(company_b)), pytest.raises(
        ImportAuthorityError, match="company is immutable"
    ):
        row.save(update_fields=["source_company"])

    table = connection.ops.quote_name(CompanyOwnedImportRow._meta.db_table)
    company_column = connection.ops.quote_name("source_company_id")
    with transaction.atomic(), import_operation(operation(company_b)), connection.cursor() as cursor, pytest.raises(
        DatabaseError, match="company is immutable|source-owned mutation"
    ):
        cursor.execute(
            f"UPDATE {table} SET {company_column} = %s WHERE id = %s",
            (company_b.pk, row.pk),
        )


@pytest.mark.django_db(transaction=True)
def test_named_additive_guards_do_not_replace_primary_guard(owned_table: None) -> None:
    declaration = ExternalOwnershipDeclaration(
        source_owned_fields={"name"},
        operations={"fixture.import"},
    )
    with connection.schema_editor() as schema_editor:
        install_external_ownership_guard(
            schema_editor,
            OwnedImportRow,
            declaration,
            guard_key="addon.field",
        )
    try:
        if connection.vendor == "sqlite":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND tbl_name = %s AND name LIKE %s",
                    [OwnedImportRow._meta.db_table, "angee_external_owner_%"],
                )
                assert cursor.fetchone()[0] == 6
    finally:
        with connection.schema_editor() as schema_editor:
            remove_external_ownership_guard(
                schema_editor,
                OwnedImportRow,
                guard_key="addon.field",
            )


@pytest.mark.django_db(transaction=True)
def test_local_overlay_bulk_and_database_paths_preserve_ownership(owned_table: None) -> None:
    """Local overlays remain writable while bulk/raw source mutations are denied."""

    row = OwnedImportRow(
        name="source name",
        external_source_type="integrate.fixture",
        external_source_id="source-1",
        external_source_key="remote-8",
    )
    with transaction.atomic(), import_operation(_operation()):
        row.save()

    OwnedImportRow.objects.sudo(reason="test.local-overlay").filter(pk=row.pk).update(note="reviewed")
    with pytest.raises(ImportAuthorityError):
        OwnedImportRow.objects.sudo(reason="test.source-field").filter(pk=row.pk).update(name="forged")

    table = connection.ops.quote_name(OwnedImportRow._meta.db_table)
    with connection.cursor() as cursor, pytest.raises(DatabaseError, match="source-owned mutation"):
        cursor.execute(f"UPDATE {table} SET name = %s WHERE id = %s", ("raw-forged", row.pk))


@pytest.mark.django_db(transaction=True)
def test_default_manager_guards_bulk_create_update_and_delete(owned_table: None) -> None:
    """The inherited default manager cannot bypass source ownership in bulk APIs."""

    row = OwnedImportRow(
        name="bulk source",
        external_source_type="integrate.fixture",
        external_source_id="source-1",
        external_source_key="remote-bulk",
    )
    with pytest.raises(ImportAuthorityError):
        OwnedImportRow.objects.bulk_create([row])
    with transaction.atomic(), import_operation(_operation()):
        [row] = OwnedImportRow.objects.sudo(reason="test.bulk-create").bulk_create([row])

    row.name = "bulk refresh"
    with pytest.raises(ImportAuthorityError):
        OwnedImportRow.objects.bulk_update([row], ["name"])
    with transaction.atomic(), import_operation(_operation()):
        assert OwnedImportRow.objects.sudo(reason="test.bulk-update").bulk_update([row], ["name"]) == 1

    with pytest.raises(ImportAuthorityError):
        OwnedImportRow.objects.sudo(reason="test.bulk-delete").filter(pk=row.pk).delete()
    with transaction.atomic(), import_operation(_operation()):
        deleted, _ = OwnedImportRow.objects.sudo(reason="test.bulk-delete").filter(pk=row.pk).delete()
    assert deleted == 1


@pytest.mark.django_db(transaction=True)
def test_existing_natural_key_row_can_only_be_claimed_once_atomically(owned_table: None) -> None:
    """Reference adoption permits one authorized empty-to-complete provenance transition."""

    row = OwnedImportRow.objects.sudo(reason="test.reference-create").create(name="existing reference")
    with pytest.raises(ImportAuthorityError):
        row.claim_external_ownership("remote-existing", "fixture.import")
    with transaction.atomic(), import_operation(_operation()):
        row.claim_external_ownership("remote-existing", "fixture.import")
    persisted = OwnedImportRow.objects.sudo(reason="test.claim").get(pk=row.pk)
    assert (
        persisted.external_source_type,
        persisted.external_source_id,
        persisted.external_source_key,
    ) == ("integrate.fixture", "source-1", "remote-existing")
    with (
        transaction.atomic(),
        import_operation(_operation()),
        pytest.raises(ImportAuthorityError, match="already claimed"),
    ):
        row.claim_external_ownership("replacement", "fixture.import")


@pytest.mark.django_db(transaction=True)
def test_authority_is_restored_after_nested_scope_and_cleared_after_rollback(owned_table: None) -> None:
    """Nested scopes restore exactly and rollback cannot leak a capability."""

    outer = _operation()
    inner = _operation(source_id="source-2")
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic(), import_operation(outer):
            assert current_import_operation() == outer
            with import_operation(inner):
                assert current_import_operation() == inner
            assert current_import_operation() == outer
            raise RuntimeError("rollback")
    assert current_import_operation() is None


@pytest.mark.django_db(transaction=True)
def test_mti_child_guard_reads_parent_provenance_and_donor_local_fields(owned_table: None) -> None:
    """Physical child tables remain guarded while declared addon overlays stay local."""

    child = OwnedImportChild(
        name="parent fact",
        detail="child fact",
        external_source_type="integrate.fixture",
        external_source_id="source-1",
        external_source_key="remote-child",
    )
    with (
        transaction.atomic(),
        import_operation(
            ImportOperation(
                source=ImportSource("integrate.fixture", "source-1"),
                company=None,
                operation="fixture.import",
                run="run-child",
                targets=(ImportTarget("tests.ownedimportchild"), ImportTarget("tests.ownedimportrow")),
            )
        ),
    ):
        child.save()
    child_table = connection.ops.quote_name(OwnedImportChild._meta.db_table)
    with connection.cursor() as cursor, pytest.raises(DatabaseError, match="source-owned mutation"):
        cursor.execute(f"UPDATE {child_table} SET detail = %s WHERE ownedimportrow_ptr_id = %s", ("forged", child.pk))

    donated = DonatedOwnedImportRow(name="local base", review="pending")
    donated.save()
    donated.review = "accepted"
    donated.save(update_fields=["review"])


@pytest.mark.django_db(transaction=True)
def test_many_to_many_edges_require_owner_authority_in_orm_and_raw_sql(owned_table: None) -> None:
    """New or removed through rows mutate their source-owned endpoint."""

    row = OwnedImportRow(
        name="source name",
        external_source_type="integrate.fixture",
        external_source_id="source-1",
        external_source_key="remote-tags",
    )
    with transaction.atomic(), import_operation(_operation()):
        row.save()
    tag = OwnedTag.objects.create(name="tax")

    with pytest.raises(ImportAuthorityError):
        row.tags.add(tag)
    with transaction.atomic(), import_operation(_operation()):
        row.tags.add(tag)
    with pytest.raises(ImportAuthorityError):
        row.tags.remove(tag)

    through = OwnedImportRow.tags.through
    table = connection.ops.quote_name(through._meta.db_table)
    owner_column = connection.ops.quote_name(OwnedImportRow.tags.field.m2m_column_name())
    tag_column = connection.ops.quote_name(OwnedImportRow.tags.field.m2m_reverse_name())
    with connection.cursor() as cursor, pytest.raises(DatabaseError, match="source-owned relation mutation"):
        cursor.execute(
            f"INSERT INTO {table} ({owner_column}, {tag_column}) VALUES (%s, %s)",
            (row.pk, tag.pk),
        )


@pytest.mark.django_db(transaction=True)
def test_unclaimed_foreign_key_edge_cannot_mutate_owned_endpoint(owned_table: None) -> None:
    """Endpoint protection does not depend on the new edge carrying provenance."""

    row = OwnedImportRow(
        name="source name",
        external_source_type="integrate.fixture",
        external_source_id="source-1",
        external_source_key="remote-edge",
    )
    with transaction.atomic(), import_operation(_operation()):
        row.save()
    with pytest.raises(ImportAuthorityError, match="endpoint"):
        OwnedImportEdge(endpoint=row, amount=3).save()
    with transaction.atomic(), import_operation(_operation()):
        edge = OwnedImportEdge(endpoint=row, amount=3)
        edge.save()

    # An unrelated field update does not mutate the protected endpoint and must
    # not require the endpoint's import operation.
    OwnedImportEdge.objects.sudo(reason="test.local-edge-note").filter(pk=edge.pk).update(note="reviewed")
    assert OwnedImportEdge.objects.sudo(reason="test.verify").get(pk=edge.pk).note == "reviewed"

    table = connection.ops.quote_name(OwnedImportEdge._meta.db_table)
    with connection.cursor() as cursor, pytest.raises(DatabaseError, match="source-owned mutation"):
        cursor.execute(f"INSERT INTO {table} (endpoint_id, amount) VALUES (%s, %s)", (row.pk, 4))
