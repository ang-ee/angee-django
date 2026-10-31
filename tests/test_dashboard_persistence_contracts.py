"""Dashboard factories and authored verbs adopt the shared persistence rules."""

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from rebac import PermissionDenied, actor_context, system_context

from angee.base.models.locks import StaleRevisionError
from angee.dashboards.exceptions import DashboardConflictError
from angee.graphql.schema import GraphQLSchemas
from angee.provenance.context import changeset, changeset_model
from tests.conftest import create_user
from tests.test_dashboards import DashboardTarget, dashboard_tables  # noqa: F401

pytestmark = pytest.mark.usefixtures("dashboard_tables")


def test_personal_idempotency_replays_within_owner_scope():
    first, second = create_user("first"), create_user("second")
    with actor_context(first):
        created = DashboardTarget.objects.create_personal(first, name="Original", idempotency_key="request")
        replay = DashboardTarget.objects.create_personal(first, name="Original", idempotency_key="request")
        assert (replay.pk, replay.name, replay.revision) == (created.pk, "Original", 1)
        claim = changeset_model()._base_manager.get(key="request", created_object_id=str(created.pk))
        assert claim.created_row.pk == created.pk
    with actor_context(second):
        separate = DashboardTarget.objects.create_personal(second, name="Separate", idempotency_key="request")
        assert separate.pk != created.pk


def test_personal_creation_still_validates_key_field_length():
    owner = create_user("owner")
    with actor_context(owner):
        with pytest.raises(ValidationError) as caught:
            DashboardTarget.objects.create_personal(owner, name="Original", idempotency_key="x" * 129)
        assert "idempotency_key" in caught.value.message_dict
        assert not DashboardTarget.objects.exists()


def test_shared_trash_bumps_the_revision_once_per_change_and_refuses_a_repeat():
    """The shared trash owner moves a personal dashboard; each change counts one revision."""

    owner = create_user("owner")
    with actor_context(owner):
        row = DashboardTarget.objects.create_personal(owner, name="Original", idempotency_key="request")
        row.trash()
        assert (row.is_trashed, row.revision) == (True, 2)
        with pytest.raises(ValidationError):
            row.trash()
        assert DashboardTarget._base_manager.get(pk=row.pk).revision == 2
        row.restore()
        assert (row.is_trashed, row.revision) == (False, 3)


def test_snapshot_update_bumps_once_and_missing_or_stale_expectation_keeps_content(monkeypatch):
    # This empty snapshot names no composed GraphQL resources (the same seam
    # used by the dashboard resource-reload test under bare test settings).
    monkeypatch.setattr(GraphQLSchemas, "resources", lambda self, name: ())
    owner = create_user("owner")
    with actor_context(owner):
        row = DashboardTarget.objects.create_personal(owner, name="Original", idempotency_key="request")
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
    class NewWriteRouter:
        def db_for_write(self, model, **hints):
            return "elsewhere"

    owner = create_user("owner")
    with system_context(reason="test.dashboard.scoped"):
        row = DashboardTarget.objects.create(owner=owner, scope="addon", scope_key="demo", name="Scoped")
        row.save()
    with (
        override_settings(DATABASE_ROUTERS=[NewWriteRouter()]),
        actor_context(owner),
        changeset(fresh=True, using="default"),
    ):
        with pytest.raises(StaleRevisionError) as caught:
            row.reset_snapshot(expected_revision=1)
        assert caught.value.current == 2
        assert DashboardTarget.objects.filter(pk=row.pk).exists()
        row.reset_snapshot(expected_revision=2)
        assert not DashboardTarget.objects.filter(pk=row.pk).exists()


def test_copy_uses_displayed_widgets_and_replays_the_persisted_result(monkeypatch):
    monkeypatch.setattr(GraphQLSchemas, "resources", lambda self, name: ())
    owner = create_user("copy-owner")
    with actor_context(owner):
        source = DashboardTarget.objects.create_personal(owner, name="Source", idempotency_key="source")
        widget = {
            "schemaVersion": 1, "id": "declared", "definitionRef": "demo/newer",
            "kind": "panel", "kindVersion": 1, "title": "New declaration",
            "data": {"shape": "none", "binding": {"dashboardKey": "demo", "widgetId": "newer"}},
            "options": {}, "x": 0, "y": 0, "w": 3, "h": 2, "isArchived": False,
        }
        displayed = {"schemaVersion": 1, "columns": 6, "widgets": [
            widget, {**widget, "id": "removed", "isArchived": True},
        ]}
        copied = DashboardTarget.objects.duplicate_personal(
            owner, source=source, name="Copy", idempotency_key="copy", snapshot=displayed,
        )
        assert source.snapshot()["widgets"] == []
        assert copied.snapshot()["widgets"] == [{
            key: value for key, value in {**widget, "id": "copy-1"}.items() if key != "definitionRef"
        }]
        replay = DashboardTarget.objects.duplicate_personal(
            owner, source=source, name="Copy", idempotency_key="copy",
            snapshot={"schemaVersion": 1, "columns": 12, "widgets": []},
        )
        assert (replay.pk, replay.revision, replay.snapshot()) == (copied.pk, copied.revision, copied.snapshot())


def test_copy_checks_source_readability_and_rolls_back_creation_on_invalid_query(monkeypatch):
    monkeypatch.setattr(GraphQLSchemas, "resources", lambda self, name: ())
    owner, outsider = create_user("copy-owner"), create_user("copy-outsider")
    with actor_context(owner):
        source = DashboardTarget.objects.create_personal(owner, name="Source", idempotency_key="source")
    empty = {"schemaVersion": 1, "columns": 6, "widgets": []}
    with actor_context(outsider), pytest.raises(PermissionDenied):
        DashboardTarget.objects.duplicate_personal(
            outsider, source=source, name="Unreadable", idempotency_key="unreadable", snapshot=empty,
        )
    invalid_query = {**empty, "widgets": [{
        "schemaVersion": 1, "id": "bad-query", "kind": "number", "kindVersion": 1, "title": "Invalid",
        "data": {"shape": "value", "source": {"resource": "unavailable.Model"}},
        "options": {}, "x": 0, "y": 0, "w": 3, "h": 2, "isArchived": False,
    }]}
    with actor_context(owner):
        with pytest.raises(ValidationError, match="unknown console resource"):
            DashboardTarget.objects.duplicate_personal(
                owner, source=source, name="Invalid", idempotency_key="invalid", snapshot=invalid_query,
            )
        assert list(DashboardTarget.objects.values_list("name", flat=True)) == ["Source"]
        assert not changeset_model()._base_manager.filter(key="invalid").exists()
