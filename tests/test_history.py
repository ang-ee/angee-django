"""Stored history values survive actor redaction and transactional deletion."""

import pytest
from django.db import transaction
from rebac import actor_context
from rebac.backends import reset_backend
from rebac.schema import parse_zed
from rebac.testing import install_schema

from tests.conftest import create_user
from tests.core_persistence import OwnedRow


class OwnedRowProxy(OwnedRow):
    """A tracked proxy exercising the concrete table's history owner."""

    class Meta:
        proxy = True
        app_label = "scopedemo"


@pytest.fixture(params=("registry", "denormalized"))
def history_permissions(db, settings, request):
    """Use the existing persistence probe with a deliberately unreadable title."""

    settings.REBAC_LOCAL_BACKEND_STORAGE = request.param
    install_schema(
        parse_zed("""
        definition auth/user {}
        definition scopedemo/owned_row {
            relation owner: auth/user // rebac:field=owner
            relation title_reader: auth/user
            permission create = authenticated
            permission read = owner
            permission write = owner
            permission delete = owner
            permission read__title = title_reader
        }
    """)
    )
    try:
        yield
    finally:
        reset_backend()


@pytest.mark.parametrize("queryset_delete", (False, True))
def test_delete_snapshot_retains_stored_value_and_rolls_back(history_permissions, queryset_delete):
    actor = create_user("history-owner")
    with actor_context(actor):
        row = OwnedRow.objects.create(title="Stored secret")
        row_pk = row.pk
        history = row.history.model.objects.filter(id=row_pk)
        visible = OwnedRow.objects.get(pk=row_pk)
        assert visible.title is None
        with pytest.raises(RuntimeError, match="rollback"), transaction.atomic():
            if queryset_delete:
                OwnedRow.objects.filter(pk=row_pk).delete()
            else:
                visible.delete()
            assert history.get(history_type="-").title == "Stored secret"
            raise RuntimeError("rollback")
        assert not history.filter(history_type="-").exists()
        visible = OwnedRow.objects.get(pk=row_pk)
        if queryset_delete:
            OwnedRow.objects.filter(pk=row_pk).delete()
        else:
            visible.delete()
        assert visible.title is None
    assert history.get(history_type="-").title == "Stored secret"
    assert not history.model._meta.get_field("title").null
    assert not OwnedRow._base_manager.filter(pk=row_pk).exists()


def test_proxy_queryset_delete_uses_concrete_history_owner(history_permissions):
    """A proxy delete snapshots the concrete table instead of refusing tracked writes."""

    actor = create_user("proxy-history-owner")
    with actor_context(actor):
        row = OwnedRow.objects.create(title="Tracked through proxy")
        deleted, _ = OwnedRowProxy.objects.filter(pk=row.pk).delete()

    assert deleted == 1
    assert OwnedRow.history.filter(id=row.pk, history_type="-").exists()


@pytest.mark.django_db
def test_disabling_new_capture_does_not_release_retained_reference_evidence(settings):
    """The capture switch cannot make a retained foreign-key version disappear."""

    from rebac import system_context

    from angee.models.merge_references import ForeignKeyReference, MergePolicy
    from tests.core_persistence import OwnershipContainer

    with system_context(reason="tests.history.retention"):
        container = OwnershipContainer.objects.create()
        row = OwnedRow.objects.create(container=container, title="retained")
        row.delete()
    reference = ForeignKeyReference(OwnedRow, MergePolicy.MOVE, OwnedRow._meta.get_field("container"))
    assert reference.retains(container)
    settings.SIMPLE_HISTORY_ENABLED = False
    assert reference.retains(container)


@pytest.mark.django_db
def test_native_bulk_history_reads_stored_values_instead_of_unsaved_projection():
    """Native bulk callers receive the same stored-snapshot guarantee as queryset writes."""

    from rebac import system_context

    with system_context(reason="tests.history.native_bulk"):
        row = OwnedRow.objects.create(title="stored")
        row.title = "unsaved projection"
        snapshots = OwnedRow.history.bulk_history_create([row], update=True)
        count = row.history.count()
        with pytest.raises(TypeError, match="multiple values.*title"):
            OwnedRow.history.bulk_history_create([row], update=True, custom_historical_attrs={"title": "forged"})
        assert row.history.count() == count
    assert len(snapshots) == 1
    assert snapshots[0].title == "stored"


@pytest.mark.django_db
@pytest.mark.parametrize("bulk", [False, True])
def test_native_bulk_history_preserves_declared_per_instance_metadata(bulk):
    from datetime import timedelta

    from django.utils import timezone
    from rebac import system_context

    from angee.provenance.context import current_changeset

    user = create_user("native-history-user")
    when = timezone.now() - timedelta(days=1)
    with system_context(reason="tests.history.instance_metadata"):
        expected_reason = current_changeset(required=True).change_reason
        row = OwnedRow.objects.create(title="stored")
        row._history_date = when
        row._history_user = user
        row._change_reason = "instance.reason"
        other = OwnedRow.objects.create(title="other stored")
        if bulk:
            snapshots = OwnedRow.history.bulk_history_create([row, other], update=True)
        else:
            row.title = "stored after save"
            row.save()
            other.save()
            snapshots = [OwnedRow.history.filter(id=obj.pk).latest("history_id") for obj in (row, other)]
    assert snapshots[0].history_date == when
    assert snapshots[0].history_user_id == user.pk
    assert snapshots[0].history_change_reason == "instance.reason"
    assert snapshots[1].history_date != when
    assert snapshots[1].history_user_id is None
    assert snapshots[1].history_change_reason == expected_reason


@pytest.mark.django_db
def test_native_bulk_history_defaults_reach_every_tracked_mti_table(composed_tables):
    from datetime import timedelta

    from django.utils import timezone
    from rebac import system_context

    from angee.messaging.testing.models import Organization, Party

    user = create_user("native-history-default-user")
    when = timezone.now() - timedelta(days=2)
    with system_context(reason="tests.history.default_metadata"):
        organization = Organization.objects.create(display_name="Native defaults")
        Party.history.bulk_history_create(
            [organization],
            update=True,
            default_user=user,
            default_change_reason="native.default",
            default_date=when,
        )
    for model in (Party, Organization):
        snapshot = model.history.filter(**{model._meta.pk.attname: organization.pk}).latest("history_id")
        assert snapshot.history_type == "~"
        assert snapshot.history_date == when
        assert snapshot.history_user_id == user.pk
        assert snapshot.history_change_reason == "native.default"


@pytest.mark.entrypoint_boundaries
@pytest.mark.django_db(transaction=True)
def test_native_bulk_capture_can_be_unattributed_when_storage_policy_allows(settings):
    from rebac import system_context

    from angee.provenance.context import current_changeset

    settings.REBAC_STRICT_MODE = False
    assert current_changeset() is None
    with system_context(reason="tests.history.optional_attribution"):
        row = OwnedRow.objects.create(title="unattributed stored value")
        snapshots = OwnedRow.history.bulk_history_create([row], update=True)
    assert snapshots[0].title == "unattributed stored value"
    assert snapshots[0].changeset_id is None


@pytest.mark.django_db
def test_hierarchy_history_distinguishes_final_creation_from_reparenting(composed_tables):
    from tests.conftest import Page, vault_for

    owner = create_user("hierarchy-history-owner")
    vault = vault_for(owner)
    with actor_context(owner):
        parent = Page.objects.create_in(vault, title="Destination", kind="folder")
        page = Page.objects.create_in(vault, title="Moved folder", kind="folder")
        created = page.history.get()
        assert created.history_type == "+"
        assert created.path == page.path
        assert page.path

        page.parent = parent
        page.save(update_fields=["parent"])
        page.refresh_from_db()
        moved = page.history.latest("history_id")
        assert moved.history_type == "~"
        assert moved.path == page.path
        assert page.path.startswith(parent.path)
        assert moved.path != created.path
