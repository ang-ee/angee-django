"""Installed and authored dashboards share native actor-scoped reads."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import tablib
from django.apps import apps
from django.db import models
from django.test import override_settings
from rebac import actor_context, system_context
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.models import active_relationship_model
from rebac.schema import parse_zed

from angee.dashboards.models import Dashboard as AbstractDashboard
from angee.dashboards.models import DashboardWidget as AbstractDashboardWidget
from angee.graphql.schema import GraphQLSchemas
from angee.resources.entries import ResourceEntry, ResourceGroup
from angee.resources.models import Resource
from angee.testing.permissions import install_permission_schema
from tests.conftest import create_user


class DashboardTarget(AbstractDashboard):
    """Production dashboard behavior in the installed test app registry."""

    class Meta(AbstractDashboard.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "dashboards/dashboard"


class DashboardTargetWidget(AbstractDashboardWidget):
    """Production widget behavior pointing at the concrete test dashboard."""

    dashboard = models.ForeignKey(DashboardTarget, on_delete=models.CASCADE, related_name="widgets")

    class Meta(AbstractDashboardWidget.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "dashboards/widget"


class DashboardResourceLedger(Resource):
    """Concrete import ledger for replaying unchanged installed resources."""

    class Meta(Resource.Meta):
        abstract = False
        app_label = "resources"


@pytest.fixture()
def dashboard_tables(transactional_db: Any) -> Iterator[None]:
    """Load the production dashboard policy before creating dashboard rows."""

    del transactional_db
    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    policy = Path(__file__).parents[1] / "addons/angee/dashboards/permissions.zed"
    install_permission_schema(
        parse_zed(
            "definition auth/user {}\n"
            "definition auth/group { relation member: auth/user }\n"
            "definition angee/role { relation member: auth/user }\n" + policy.read_text()
        ),
        active=active,
    )
    try:
        yield
    finally:
        reset_backend()


@pytest.mark.parametrize("scope", ["addon", "resource"])
@pytest.mark.parametrize("allow_sudo", [True, False])
def test_for_target_preserves_authored_precedence_with_scoped_installed_reads(
    dashboard_tables: None, scope: str, allow_sudo: bool
) -> None:
    """Shared baselines are ordinary readable rows and never grant write access."""

    alice = create_user("dashboard-alice")
    bob = create_user("dashboard-bob")
    with system_context(reason="test.dashboards.target.seed"):
        authored = DashboardTarget.objects.create(owner=alice, name="Authored", scope=scope, scope_key="target")
        installed = DashboardTarget.objects.create(
            is_baseline=True, name="Installed", scope=scope, scope_key="target",
        )
        DashboardTarget.objects.create(owner=bob, name="Other", scope=scope, scope_key="other-owner")
        DashboardTarget.objects.create(is_baseline=True, name="Other key", scope=scope, scope_key="other-key")
        DashboardTarget.objects.create(
            is_baseline=True, name="Other scope", scope="resource" if scope == "addon" else "addon",
            scope_key="missing",
        )
        DashboardTargetWidget.objects.create(
            dashboard=installed, widget_key="welcome", kind="markdown", title="Welcome", data={}
        )

    with (
        override_settings(REBAC_ALLOW_SUDO=allow_sudo),
        patch.object(DashboardTarget, "system_queryset", side_effect=AssertionError("unexpected read elevation")),
        patch.object(
            DashboardTargetWidget, "system_queryset", side_effect=AssertionError("unexpected widget elevation")
        ),
    ):
        with actor_context(alice):
            selected = DashboardTarget.objects.for_target(alice, scope, "target")
            assert selected == authored
            assert selected.actor() == authored.with_actor(alice).actor()
            assert selected.has_access("write")
            assert DashboardTarget.objects.for_target(alice, scope, "other-owner") is None
            assert DashboardTarget.objects.for_target(alice, scope, "missing") is None
            with pytest.raises(ValueError, match="Personal dashboards resolve by public id"):
                DashboardTarget.objects.for_target(alice, "personal", None)

        with actor_context(bob):
            assert DashboardTarget.objects.filter(pk=installed.pk).exists()
            assert not DashboardTarget.objects.filter(pk=authored.pk).exists()
            selected = DashboardTarget.objects.for_target(bob, scope, "target")
            assert selected == installed
            assert not selected.is_sudo()
            assert not selected.has_access("write")
            assert [widget["id"] for widget in selected.snapshot()["widgets"]] == ["welcome"]


def test_dashboard_owner_changes_never_share_an_authored_snapshot(dashboard_tables: None) -> None:
    """Dirty and cleared owners cannot turn authored content into a baseline."""

    owner = create_user("dashboard-owner")
    outsider = create_user("dashboard-outsider")
    with system_context(reason="test.dashboards.owner"):
        dashboard = DashboardTarget.objects.create(
            owner=owner, name="Authored", scope="addon", scope_key="owner-change",
        )
        baseline = DashboardTarget.objects.create(
            is_baseline=True, name="Baseline", scope="addon", scope_key="owner-change",
        )
        dashboard.owner = None
        dashboard.name = "Renamed"
        dashboard.save(update_fields=["name"])
    with actor_context(outsider):
        assert not DashboardTarget.objects.filter(pk=dashboard.pk).exists()
        assert DashboardTarget.objects.for_target(outsider, "addon", "owner-change") == baseline
    with actor_context(owner):
        assert DashboardTarget.objects.filter(pk=dashboard.pk).exists()
    with actor_context(owner):
        dashboard.with_actor(owner).transfer_ownership(None)
        dashboard.refresh_from_db()
        assert dashboard.owner_id is None
        assert not dashboard.is_baseline
    with actor_context(outsider):
        assert not DashboardTarget.objects.filter(pk=dashboard.pk).exists()
        assert DashboardTarget.objects.for_target(outsider, "addon", "owner-change") == baseline


def test_shared_baseline_creation_requires_the_resource_system_path(dashboard_tables: None) -> None:
    owner = create_user("dashboard-baseline-candidate")
    with actor_context(owner):
        candidate = DashboardTarget(is_baseline=True, name="Shared", scope="addon", scope_key="candidate")
        assert not candidate.has_access("create")
        assert DashboardTarget(name="Personal", scope="personal").is_baseline is False


def test_dashboard_owner_deletion_preserves_private_content_and_baseline(dashboard_tables: None) -> None:
    owner = create_user("dashboard-deleted-owner")
    outsider = create_user("dashboard-deleted-reader")
    with system_context(reason="test.dashboards.owner.delete"):
        dashboard = DashboardTarget.objects.create(owner=owner, name="Private", scope="resource", scope_key="same")
        baseline = DashboardTarget.objects.create(is_baseline=True, name="Public", scope="resource", scope_key="same")
        owner.delete()
        dashboard.refresh_from_db()
        assert dashboard.owner_id is None
        assert not dashboard.is_baseline
    with actor_context(outsider):
        assert not DashboardTarget.objects.filter(pk=dashboard.pk).exists()
        assert DashboardTarget.objects.for_target(outsider, "resource", "same") == baseline


def test_dashboard_policy_bulk_writes_keep_baseline_separate_from_ownership(dashboard_tables: None) -> None:
    owner = create_user("dashboard-bulk-owner")
    outsider = create_user("dashboard-bulk-reader")
    with system_context(reason="test.dashboards.bulk"):
        dashboard = DashboardTarget.objects.create(owner=owner, name="Authored", scope="addon", scope_key="bulk")
        assert DashboardTarget.objects.filter(pk=dashboard.pk).update(owner_id=None) == 1
        (baseline,) = DashboardTarget.objects.bulk_create([
            DashboardTarget(is_baseline=True, name="Baseline", scope="addon", scope_key="other"),
        ])
    with actor_context(outsider):
        assert list(DashboardTarget.objects.values_list("pk", flat=True)) == [baseline.pk]
    with system_context(reason="test.dashboards.bulk.own"):
        assert DashboardTarget.objects.filter(pk=dashboard.pk).update(owner=owner) == 1
    with actor_context(owner):
        assert set(DashboardTarget.objects.values_list("pk", flat=True)) == {dashboard.pk, baseline.pk}
    assert not active_relationship_model().objects.filter(resource_type="dashboards/dashboard").exists()


def test_personal_dashboard_trash_retains_widgets_and_restores_under_revision(dashboard_tables: None) -> None:
    owner = create_user("dashboard-trash-owner")
    with system_context(reason="test.dashboards.trash.seed"):
        dashboard = DashboardTarget.objects.create(owner=owner, name="Personal", scope="personal")
        widget = DashboardTargetWidget.objects.create(
            dashboard=dashboard, widget_key="body", kind="markdown", title="Body", data={},
        )
    with actor_context(owner):
        removed = DashboardTarget.objects.trash_targets().get(pk=dashboard.pk)
        removed.trash()
        assert removed.is_trashed
        assert removed.trashed_by_id == owner.pk
        assert DashboardTargetWidget.objects.filter(pk=widget.pk).exists()
        removed.restore()
        assert not removed.is_trashed
        assert removed.trashed_at is None
        assert removed.revision > dashboard.revision
        assert DashboardTargetWidget in removed.history_scope()


def test_resource_reload_preserves_unchanged_installed_reads_without_tuples(
    dashboard_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Initial and unchanged resource imports need no stored shared readers."""

    # This empty dashboard names no composed console resources.
    monkeypatch.setattr(GraphQLSchemas, "resources", lambda self, name: ())
    outsider = create_user("dashboard-reload-reader")
    addon = apps.get_app_config("portfolio")
    entry = ResourceEntry(
        addon=addon,
        tier=Resource.Tier.INSTALL,
        source_value="resources/install/dashboard.yaml",
        model=DashboardTarget._meta.label,
    )
    dataset = tablib.Dataset(
        ["baseline", "Baseline", "addon", "reload"],
        headers=["_xref", "name", "scope", "scope_key"],
    )
    group = ResourceGroup(entry, DashboardTarget._meta.label, dataset, [1])
    for expected_created in (1, 0):
        result = DashboardResourceLedger.objects._import_groups(
            (entry,), (group,), (), dry_run=False, addon_aliases={addon.label: addon.name}
        )
        assert result.created == expected_created
        assert result.skipped == 1 - expected_created
        with actor_context(outsider):
            dashboard = DashboardTarget.objects.get(scope_key="reload")
        assert not active_relationship_model().objects.filter(
            resource_type="dashboards/dashboard", resource_id=str(dashboard.pk), relation="shared"
        ).exists()
