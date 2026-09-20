"""Dashboard target selection retains native authored and installed read scopes."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import connection, models
from django.test import override_settings
from rebac import RelationshipTuple, SubjectRef, actor_context, system_context, to_object_ref, write_relationships
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.schema import parse_zed

from angee.base.models import AngeeDataModel
from angee.dashboards.models import DashboardManager
from tests.conftest import _clear_model_tables, _create_missing_tables


class DashboardTarget(AngeeDataModel):
    """Minimal row shape exercising the dashboard manager with real REBAC queries."""

    sqid_prefix = "dst_"
    owner = models.IntegerField(null=True)
    scope = models.CharField(max_length=16)
    scope_key = models.CharField(max_length=255)

    objects = DashboardManager()

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "tests"
        rebac_resource_type = "tests/dashboard_target"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("scope", ["addon", "resource"])
@pytest.mark.parametrize("allow_sudo", [True, False])
def test_for_target_preserves_authored_precedence_and_installed_elevation(scope: str, allow_sudo: bool) -> None:
    """Only the installed fallback bypasses read policy, even when user sudo is disabled."""

    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    active.set_schema(
        parse_zed(
            """
            definition auth/user {}
            definition tests/dashboard_target {
                relation reader: auth/user
                relation writer: auth/user
                permission read = reader
                permission write = writer
            }
            """
        )
    )
    created = _create_missing_tables((DashboardTarget,))
    try:
        alice = SubjectRef.of("auth/user", "alice")
        bob = SubjectRef.of("auth/user", "bob")
        with system_context(reason="test.dashboards.target.seed"):
            authored = DashboardTarget.objects.create(owner=1, scope=scope, scope_key="target")
            installed = DashboardTarget.objects.create(owner=None, scope=scope, scope_key="target")
            other_owner = DashboardTarget.objects.create(owner=2, scope=scope, scope_key="other-owner")
            DashboardTarget.objects.create(owner=None, scope=scope, scope_key="other-key")
            DashboardTarget.objects.create(
                owner=None, scope="resource" if scope == "addon" else "addon", scope_key="missing"
            )
        write_relationships(
            [
                RelationshipTuple(to_object_ref(authored), "reader", alice),
                RelationshipTuple(to_object_ref(other_owner), "reader", alice),
            ]
        )

        with override_settings(REBAC_ALLOW_SUDO=allow_sudo):
            with actor_context(alice):
                with patch.object(
                    DashboardTarget, "system_queryset", side_effect=AssertionError("unexpected fallback")
                ):
                    selected = DashboardTarget.objects.for_target(1, scope, "target")
                assert selected == authored
                assert selected.actor() == alice
                assert not selected.has_access("write")
                assert DashboardTarget.objects.for_target(1, scope, "other-owner") is None
                assert DashboardTarget.objects.for_target(1, scope, "missing") is None
                with pytest.raises(ValueError, match="Personal dashboards resolve by public id"):
                    DashboardTarget.objects.for_target(1, "personal", None)

            with actor_context(bob):
                assert not DashboardTarget.objects.exists()
                selected = DashboardTarget.objects.for_target(1, scope, "target")
                assert selected == installed
                assert not selected.is_sudo()
                assert not selected.has_access("write")
                assert not DashboardTarget.objects.exists()
    finally:
        _clear_model_tables((DashboardTarget,))
        if created:
            with connection.schema_editor() as editor:
                for model in reversed(created):
                    editor.delete_model(model)
        reset_backend()
