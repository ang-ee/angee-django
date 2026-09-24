"""Installed and authored dashboards share native actor-scoped reads."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import tablib
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection, models
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
from tests.conftest import _clear_model_tables, _create_missing_tables, create_user


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
    """Load the production dashboard policy before creating shared readers."""

    del transactional_db
    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    policy = Path(__file__).parents[1] / "addons/angee/dashboards/permissions.zed"
    active.set_schema(
        parse_zed(
            "definition auth/user {}\n"
            "definition auth/group { relation member: auth/user }\n"
            "definition angee/role { relation member: auth/user }\n" + policy.read_text()
        )
    )
    test_models = (DashboardTarget, DashboardTargetWidget, DashboardResourceLedger)
    created = _create_missing_tables(test_models)
    try:
        yield
    finally:
        _clear_model_tables(test_models)
        if created:
            with connection.schema_editor() as editor:
                for model in reversed(created):
                    editor.delete_model(model)
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
        installed = DashboardTarget.objects.create(owner=None, name="Installed", scope=scope, scope_key="target")
        DashboardTarget.objects.create(owner=bob, name="Other", scope=scope, scope_key="other-owner")
        DashboardTarget.objects.create(owner=None, name="Other key", scope=scope, scope_key="other-key")
        DashboardTarget.objects.create(
            owner=None, name="Other scope", scope="resource" if scope == "addon" else "addon", scope_key="missing"
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


def test_dashboard_owner_changes_reconcile_only_persisted_eligibility(dashboard_tables: None) -> None:
    """Dirty and deferred owners never change readers until ownership is saved."""

    owner = create_user("dashboard-owner")
    outsider = create_user("dashboard-outsider")
    with system_context(reason="test.dashboards.owner"):
        dashboard = DashboardTarget.objects.create(name="Baseline", scope="addon", scope_key="owner-change")
        dashboard.owner = owner
        dashboard.name = "Renamed"
        dashboard.save(update_fields=["name"])
    with actor_context(outsider):
        assert DashboardTarget.objects.filter(pk=dashboard.pk).exists()
    with system_context(reason="test.dashboards.owner.persist"):
        dashboard.save(update_fields=["owner"])
    with actor_context(outsider):
        assert not DashboardTarget.objects.filter(pk=dashboard.pk).exists()
    with system_context(reason="test.dashboards.owner.defer"):
        dashboard = DashboardTarget.objects.defer("owner").get(pk=dashboard.pk)
        dashboard.name = "Deferred owner"
        dashboard.save(update_fields=["name"])
        dashboard.owner = None
        dashboard.save(update_fields=["owner"])
    with actor_context(outsider):
        assert DashboardTarget.objects.filter(pk=dashboard.pk).exists()


def test_dashboard_policy_bulk_writes_are_rejected(dashboard_tables: None) -> None:
    """Bulk writes cannot bypass reader reconciliation."""

    with system_context(reason="test.dashboards.bulk"):
        dashboard = DashboardTarget.objects.create(name="Baseline", scope="addon", scope_key="bulk")
        with pytest.raises(ValidationError, match="eligibility"):
            DashboardTarget.objects.filter(pk=dashboard.pk).update(owner_id=None)
        with pytest.raises(ValidationError, match="native owner"):
            DashboardTarget.objects.bulk_create([DashboardTarget(name="Bulk", scope="addon", scope_key="other")])


def test_resource_reload_reconciles_unchanged_installed_readers(
    dashboard_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-load hooks restore readers even when the import skips an unchanged row."""

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
        if expected_created:
            with system_context(reason="test.dashboards.resource.previous-policy"):
                active_relationship_model().objects.filter(
                    resource_type="dashboards/dashboard", resource_id=str(dashboard.pk), relation="shared"
                ).delete()
            with actor_context(outsider):
                assert not DashboardTarget.objects.filter(pk=dashboard.pk).exists()
