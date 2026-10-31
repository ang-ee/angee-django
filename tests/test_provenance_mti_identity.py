"""Native parent identities scope protected document and statement history."""

import pytest
from django.apps import apps
from django.conf import settings
from django.db import models
from rebac import actor_context, system_context
from rebac.backends import reset_backend
from rebac.schema import parse_zed
from rebac.testing import install_schema

from angee.base.models.base import AngeeDataModel
from angee.provenance.context import changeset
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.statements import Aspect, SourceKind, StatementRecords, StatementsMixin
from tests.conftest import create_user
from tests.tables import model_tables


@pytest.fixture
def identity_policy(composed_tables):
    install_schema(parse_zed("""
        definition auth/user {}
        definition scopedemo/identity_parent {
            relation owner: auth/user // rebac:field=owner
            permission create = authenticated
            permission read = owner
            permission write = owner
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
        }
    """))
    try:
        yield
    finally:
        reset_backend()


@pytest.mark.usefixtures("identity_policy")
def test_distinct_parent_identity_scopes_statements_and_inverse_without_colliding_children():
    class IdentityParent(StatementsMixin, HistoryMixin, AngeeDataModel):
        title = models.CharField(max_length=80)
        owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
        statements = StatementRecords(aspects={"identity": Aspect(fields=("title",))})
        history_relations = ("identity_lines",)

        class Meta(AngeeDataModel.Meta):
            app_label = "scopedemo"
            rebac_resource_type = "scopedemo/identity_parent"

    class IdentityChild(IdentityParent):
        child_key = models.IntegerField(primary_key=True)
        detail = models.CharField(max_length=80, default="")
        statements = StatementRecords(aspects={"detail": Aspect(fields=("detail",))})

        class Meta(IdentityParent.Meta):
            app_label = "scopedemo"
            rebac_resource_type = "scopedemo/identity_child"

    class IdentityLine(HistoryMixin, AngeeDataModel):
        parent = models.ForeignKey(IdentityParent, on_delete=models.CASCADE, related_name="identity_lines")
        title = models.CharField(max_length=80)

        class Meta(AngeeDataModel.Meta):
            app_label = "scopedemo"
            rebac_resource_type = "scopedemo/identity_line"

    statement = IdentityParent.statements.model
    child_statement = IdentityChild.statements.model
    tables = (IdentityParent, IdentityParent.history.model, IdentityChild,
              IdentityChild.history.model, statement, statement.history.model,
              child_statement, child_statement.history.model, IdentityLine, IdentityLine.history.model)
    owner = create_user("parent-history-identity")
    outsider = create_user("other-parent-history-identity")
    Changeset = apps.get_model("provenance", "Changeset")
    with model_tables(tables), actor_context(owner), system_context(reason="native MTI history fixture"):
        with changeset(fresh=True):
            row = IdentityChild.objects.create(id=11, child_key=71, title="target", owner=owner)
            decoy = IdentityChild.objects.create(id=71, child_key=11, title="decoy", owner=outsider)
        with changeset(fresh=True):
            parent = IdentityParent._base_manager.get(pk=11)
            parent.title = "parent changed"
            parent.save(update_fields=["title"])
            receipt = parent.history.first().changeset
        scopes = row.history_scopes()
        assert set(IdentityParent.history.filter(scopes[IdentityParent]).values_list("id", flat=True)) == {11}
        assert receipt in Changeset.objects.touching(row)
        assert receipt not in Changeset.objects.touching(decoy)
        touched = set(Changeset.objects.filter(pk=receipt.pk).records_touched())
        assert (IdentityChild, row.pk) in touched
        assert (IdentityChild, decoy.pk) not in touched
        with changeset(fresh=True):
            retained = statement._base_manager.create(
                subject=parent, aspect="identity", title="asserted", asserted_by=str(owner.pk),
                source_kind=SourceKind.PERSON,
            )
            statement_receipt = retained.history.first().changeset
        assert statement_receipt in Changeset.objects.touching(row)
        assert statement_receipt not in Changeset.objects.touching(decoy)
        statement_touched = set(Changeset.objects.filter(pk=statement_receipt.pk).records_touched())
        assert (IdentityChild, row.pk) in statement_touched
        assert (IdentityChild, decoy.pk) not in statement_touched

        with changeset(fresh=True):
            child_retained = child_statement._base_manager.create(
                subject=row, aspect="detail", detail="child asserted", asserted_by=str(owner.pk),
                source_kind=SourceKind.PERSON,
            )
            child_receipt = child_retained.history.first().changeset
        assert child_receipt in Changeset.objects.touching(row)
        assert child_receipt not in Changeset.objects.touching(decoy)
        child_touched = set(Changeset.objects.filter(pk=child_receipt.pk).records_touched())
        assert (IdentityChild, row.pk) in child_touched
        assert (IdentityChild, decoy.pk) not in child_touched
        before = list(IdentityParent.history.filter(changeset=receipt).values())
        receipt.refresh_from_db()
        receipt.revert()
        parent.refresh_from_db()
        assert parent.title == "target"
        assert list(IdentityParent.history.filter(changeset=receipt).values()) == before

        assert row.with_actor(owner).has_access("write")
        assert not row.with_actor(outsider).has_access("write")

        with changeset(fresh=True):
            line = IdentityLine.objects.create(parent=parent, title="owned")
            line_receipt = line.history.first().changeset
        assert line_receipt in Changeset.objects.touching(row)
        assert line_receipt not in Changeset.objects.touching(decoy)
        line_touched = set(Changeset.objects.filter(pk=line_receipt.pk).records_touched())
        assert (IdentityChild, row.pk) in line_touched
        assert (IdentityChild, decoy.pk) not in line_touched
