"""Native parent identities scope protected document and statement history."""

import pytest
from django.apps import apps
from django.conf import settings
from django.db import models
from rebac import actor_context, system_context
from rebac.backends import reset_backend
from rebac.schema import parse_zed
from rebac.testing import install_schema

from angee.iam.models.base import ProtectedDataModel
from angee.provenance.context import changeset, current_changeset
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.statements import Aspect, SourceKind, StatementRecords, StatementsMixin
from tests.conftest import create_user
from tests.tables import model_tables


@pytest.fixture
def identity_policy(composed_tables):
    install_schema(
        parse_zed("""
        definition auth/user {}
        definition scopedemo/identity_parent {
            relation owner: auth/user // rebac:field=owner
            permission create = authenticated
            permission read = owner
            permission write = owner
        }
        definition scopedemo/identity_sibling {
            relation parent: scopedemo/identity_parent // rebac:field=identityparent_ptr
            permission create = parent->write
            permission read = parent->read
            permission write = parent->write
        }
        definition scopedemo/identity_line {
            relation parent: scopedemo/identity_parent // rebac:field=parent
            permission create = parent->write
            permission read = parent->read
            permission write = parent->write
        }
        definition scopedemo/identity_child {
            relation parent: scopedemo/identity_parent // rebac:field=identityparent_ptr
            permission create = parent->write
            permission read = parent->read
            permission write = parent->write
            permission delete = parent->write
        }
    """)
    )
    try:
        yield
    finally:
        reset_backend()


@pytest.fixture
def scopedemo_registration():
    """Remove this test's runtime models after its temporary tables are dropped."""

    registered = apps.all_models["scopedemo"]
    existing = set(registered)
    yield
    for name in set(registered) - existing:
        del registered[name]
    apps.clear_cache()


@pytest.mark.usefixtures("identity_policy", "scopedemo_registration")
def test_distinct_parent_identity_scopes_statements_and_inverse_without_colliding_children():
    class IdentityParent(StatementsMixin, HistoryMixin, ProtectedDataModel):
        title = models.CharField(max_length=80)
        owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
        statements = StatementRecords(aspects={"identity": Aspect(fields=("title",))})
        history_relations = ("identity_lines",)

        class Meta(ProtectedDataModel.Meta):
            app_label = "scopedemo"
            rebac_resource_type = "scopedemo/identity_parent"

    class IdentityChild(IdentityParent):
        child_key = models.IntegerField(primary_key=True)
        detail = models.CharField(max_length=80, default="")
        statements = StatementRecords(aspects={"detail": Aspect(fields=("detail",))})

        class Meta(IdentityParent.Meta):
            app_label = "scopedemo"
            rebac_resource_type = "scopedemo/identity_child"

    class IdentitySibling(IdentityParent):
        sibling_key = models.IntegerField(primary_key=True)

        class Meta(IdentityParent.Meta):
            app_label = "scopedemo"
            rebac_resource_type = "scopedemo/identity_sibling"

    class IdentityLine(HistoryMixin, ProtectedDataModel):
        parent = models.ForeignKey(IdentityParent, on_delete=models.CASCADE, related_name="identity_lines")
        title = models.CharField(max_length=80)

        class Meta(ProtectedDataModel.Meta):
            app_label = "scopedemo"
            rebac_resource_type = "scopedemo/identity_line"

    statement = IdentityParent.statements.model
    child_statement = IdentityChild.statements.model
    tables = (
        IdentityParent,
        IdentityParent.history.model,
        IdentityChild,
        IdentityChild.history.model,
        statement,
        statement.history.model,
        child_statement,
        child_statement.history.model,
        IdentityLine,
        IdentityLine.history.model,
        IdentitySibling,
        IdentitySibling.history.model,
    )
    owner = create_user("parent-history-identity")
    outsider = create_user("other-parent-history-identity")
    Changeset = apps.get_model("provenance", "Changeset")
    with model_tables(tables), actor_context(owner), system_context(reason="native MTI history fixture"):
        with changeset(fresh=True):
            row = IdentityChild.objects.create(id=11, child_key=71, title="target", owner=owner)
            decoy = IdentityChild.objects.create(id=71, child_key=11, title="decoy", owner=outsider)
            sibling = IdentitySibling.objects.create(
                **{field.attname: getattr(row, field.attname) for field in IdentityParent._meta.concrete_fields},
                sibling_key=81,
            )
            IdentitySibling.objects.create(
                **{field.attname: getattr(decoy, field.attname) for field in IdentityParent._meta.concrete_fields},
                sibling_key=11,
            )
        with changeset(fresh=True):
            parent = IdentityParent._base_manager.get(pk=11)
            parent.title = "parent changed"
            parent.save(update_fields=["title"])
            receipt = parent.history.first().history_changeset
        scopes = row.history_scopes()
        assert set(IdentityParent.history.filter(scopes[IdentityParent]).values_list("id", flat=True)) == {11}
        assert receipt in Changeset.objects.touching(row)
        assert receipt not in Changeset.objects.touching(decoy)
        touched = set(Changeset.objects.filter(pk=receipt.pk).records_touched())
        assert (IdentityChild, row.pk) in touched
        assert (IdentityChild, decoy.pk) not in touched
        with changeset(fresh=True):
            retained = statement._base_manager.create(
                subject=parent,
                aspect="identity",
                title="asserted",
                asserted_by=str(owner.pk),
                source_kind=SourceKind.PERSON,
            )
            statement_receipt = retained.history.first().history_changeset
        assert statement_receipt in Changeset.objects.touching(row)
        assert statement_receipt not in Changeset.objects.touching(decoy)
        statement_touched = set(Changeset.objects.filter(pk=statement_receipt.pk).records_touched())
        assert (IdentityChild, row.pk) in statement_touched
        assert (IdentityChild, decoy.pk) not in statement_touched

        with changeset(fresh=True):
            child_retained = child_statement._base_manager.create(
                subject=row,
                aspect="detail",
                detail="child asserted",
                asserted_by=str(owner.pk),
                source_kind=SourceKind.PERSON,
            )
            child_receipt = child_retained.history.first().history_changeset
        assert child_receipt in Changeset.objects.touching(row)
        assert child_receipt not in Changeset.objects.touching(decoy)
        child_touched = set(Changeset.objects.filter(pk=child_receipt.pk).records_touched())
        assert (IdentityChild, row.pk) in child_touched
        assert (IdentityChild, decoy.pk) not in child_touched
        before = list(IdentityParent.history.filter(history_changeset=receipt).values())
        receipt.refresh_from_db()
        receipt.revert()
        parent.refresh_from_db()
        assert parent.title == "target"
        assert list(IdentityParent.history.filter(history_changeset=receipt).values()) == before

        assert row.with_actor(owner).has_access("write")
        assert not row.with_actor(outsider).has_access("write")

        with changeset(fresh=True):
            line = IdentityLine.objects.create(parent=parent, title="owned")
            line_receipt = line.history.first().history_changeset
        assert line_receipt in Changeset.objects.touching(row)
        assert line_receipt not in Changeset.objects.touching(decoy)
        line_touched = set(Changeset.objects.filter(pk=line_receipt.pk).records_touched())
        assert (IdentityChild, row.pk) in line_touched
        assert (IdentityChild, decoy.pk) not in line_touched

        for document in (parent, row, sibling):
            views = document.history_scope()
            assert set(IdentityChild.history.filter(views[IdentityChild]).values_list("child_key", flat=True)) == {71}
            assert set(
                IdentitySibling.history.filter(views[IdentitySibling]).values_list("sibling_key", flat=True)
            ) == {81}
        for origin, expected in ((parent, True), (sibling, True), (IdentityParent._base_manager.get(pk=71), False)):
            with changeset(fresh=True, activity=Changeset.Activity.MERGE, origin=origin):
                merge_receipt = current_changeset(record=True)
            merge_receipt.refresh_from_db()
            assert (merge_receipt in Changeset.objects.touching(row)) is expected
            assert (merge_receipt.pk in row.admitted_reverts([merge_receipt])) is expected
            touched_merge = set(Changeset.objects.filter(pk=merge_receipt.pk).records_touched())
            assert ((IdentityChild, row.pk) in touched_merge) is expected
        with changeset(fresh=True):
            removable = IdentityChild.objects.create(id=21, child_key=91, title="removable", owner=owner)
            IdentityParent.objects.create(id=91, title="wrong parent", owner=outsider)
        with changeset(fresh=True):
            removable.delete(keep_parents=True)
            removed = current_changeset()
        removed.refresh_from_db()
        removed.revert()
        restored = IdentityChild._base_manager.get(pk=91)
        assert restored.id == 21
        assert restored.owner_id == owner.pk
        assert IdentityParent._base_manager.get(pk=91).owner_id == outsider.pk
