"""Dashboard factories and authored verbs adopt the shared persistence rules."""

import pytest
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context

from angee.base.mixins import StaleRevisionError
from angee.dashboards.models import DashboardConflictError
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import create_user
from tests.test_dashboards import DashboardTarget, dashboard_tables  # noqa: F401

pytestmark = pytest.mark.usefixtures("dashboard_tables")


def test_personal_creation_key_replays_within_owner_scope():
    first, second = create_user("first"), create_user("second")
    with actor_context(first):
        created = DashboardTarget.objects.create_personal(first, name="Original", client_creation_key="request")
        replay = DashboardTarget.objects.create_personal(first, name="Replay", client_creation_key="request")
        assert (replay.pk, replay.name, replay.revision) == (created.pk, "Original", 1)
        assert replay.creation_fingerprint == ""
    with actor_context(second):
        separate = DashboardTarget.objects.create_personal(second, name="Separate", client_creation_key="request")
        assert separate.pk != created.pk


def test_personal_creation_still_validates_key_field_length():
    owner = create_user("owner")
    with actor_context(owner):
        with pytest.raises(ValidationError) as caught:
            DashboardTarget.objects.create_personal(owner, name="Original", client_creation_key="x" * 129)
        assert "client_creation_key" in caught.value.message_dict
        assert not DashboardTarget.objects.exists()


def test_archive_bumps_once_noops_without_bumping_and_rejects_stale_revision():
    owner = create_user("owner")
    with actor_context(owner):
        row = DashboardTarget.objects.create_personal(owner, name="Original", client_creation_key="request")
        archived = row.set_personal_archived(archived=True, expected_revision=1)
        assert (archived.is_archived, archived.revision) == (True, 2)
        unchanged = archived.set_personal_archived(archived=True, expected_revision=2)
        assert unchanged.revision == 2
        with pytest.raises(StaleRevisionError) as caught:
            row.set_personal_archived(archived=False, expected_revision=1)
        assert (caught.value.expected, caught.value.current) == (1, 2)
        reopened = unchanged.set_personal_archived(archived=False, expected_revision=2)
        assert (reopened.is_archived, reopened.revision) == (False, 3)


def test_snapshot_update_bumps_once_and_missing_or_stale_expectation_keeps_content(monkeypatch):
    # This empty snapshot names no composed GraphQL resources (the same seam
    # used by the dashboard resource-reload test under bare test settings).
    monkeypatch.setattr(GraphQLSchemas, "resources", lambda self, name: ())
    owner = create_user("owner")
    with actor_context(owner):
        row = DashboardTarget.objects.create_personal(owner, name="Original", client_creation_key="request")
        arguments = {
            "scope": "personal", "scope_key": None, "persisted_id": row.pk,
            "snapshot": {"schemaVersion": 1, "columns": 6, "widgets": []},
        }
        updated = DashboardTarget.objects.save_snapshot(owner, expected_revision=1, **arguments)
        assert (updated.columns, updated.revision) == (6, 2)
        with pytest.raises(DashboardConflictError) as missing:
            DashboardTarget.objects.save_snapshot(owner, expected_revision=None, **arguments)
        assert missing.value.current_revision == 2
        with pytest.raises(StaleRevisionError) as stale:
            DashboardTarget.objects.save_snapshot(owner, expected_revision=1, **arguments)
        assert stale.value.current == 2
        row.refresh_from_db()
        assert (row.columns, row.revision) == (6, 2)


def test_reset_rejects_stale_revision_before_deleting_the_scoped_dashboard():
    owner = create_user("owner")
    with system_context(reason="test.dashboard.scoped"):
        row = DashboardTarget.objects.create(owner=owner, scope="addon", scope_key="demo", name="Scoped")
        row.save()
    with actor_context(owner):
        with pytest.raises(StaleRevisionError) as caught:
            DashboardTarget.objects.reset_snapshot(row, expected_revision=1)
        assert caught.value.current == 2
        assert DashboardTarget.objects.filter(pk=row.pk).exists()
        DashboardTarget.objects.reset_snapshot(row, expected_revision=2)
        assert not DashboardTarget.objects.filter(pk=row.pk).exists()
