"""Platform reflection follows the connection named by Django's migration signal."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from angee import signals as base_signals
from angee.platform import signals
from angee.platform.models import AddonManager


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("using", "allow_migrate", "table_exists", "reconciled"),
    [
        ("default", True, True, True),
        ("other", True, True, False),
        ("default", False, True, False),
        ("default", True, False, False),
    ],
)
def test_post_migrate_reconciles_only_the_matching_routed_database(
    monkeypatch: pytest.MonkeyPatch,
    using: str,
    allow_migrate: bool,
    table_exists: bool,
    reconciled: bool,
) -> None:
    """A signal for another connection must neither inspect nor write this model."""

    addon_model = Mock()
    manager = Mock(spec=AddonManager)
    addon_model._default_manager.db_manager.return_value = manager
    addon_model._meta.db_table = "platform_addon"
    table_probe = Mock(return_value=["platform_addon"] if table_exists else [])
    connection = SimpleNamespace(cursor=nullcontext, introspection=SimpleNamespace(table_names=table_probe))
    migration_router = Mock(return_value=allow_migrate)
    monkeypatch.setattr(base_signals.apps, "get_model", Mock(return_value=addon_model))
    monkeypatch.setattr(base_signals.router, "db_for_write", Mock(return_value="default"))
    monkeypatch.setattr(base_signals.router, "allow_migrate_model", migration_router)
    monkeypatch.setattr(base_signals, "connections", {"default": connection})

    signals._reconcile_addons(app_config=SimpleNamespace(label="platform"), using=using)

    assert manager.reconcile_loaded_registry.call_count == int(reconciled)
    if reconciled:
        addon_model._default_manager.db_manager.assert_called_once_with(using)
    else:
        addon_model._default_manager.db_manager.assert_not_called()
    if using != "default":
        migration_router.assert_not_called()
        table_probe.assert_not_called()
    else:
        migration_router.assert_called_once_with(using, addon_model)
        if allow_migrate:
            table_probe.assert_called_once()
        else:
            table_probe.assert_not_called()
