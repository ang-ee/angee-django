"""Platform reflection follows the connection named by Django's migration signal."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from angee.platform import signals


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
    addon_model._meta.db_table = "platform_addon"
    connection = object()
    migration_router = Mock(return_value=allow_migrate)
    table_probe = Mock(return_value=table_exists)
    monkeypatch.setattr(signals.apps, "get_model", Mock(return_value=addon_model))
    monkeypatch.setattr(signals.router, "db_for_write", Mock(return_value="default"))
    monkeypatch.setattr(signals.router, "allow_migrate_model", migration_router)
    monkeypatch.setattr(signals, "connections", {"default": connection})
    monkeypatch.setattr(signals, "_table_exists", table_probe)

    signals._reconcile_addons(app_config=SimpleNamespace(label="platform"), using=using)

    assert addon_model.objects.reconcile_loaded_registry.call_count == int(reconciled)
    if using != "default":
        migration_router.assert_not_called()
        table_probe.assert_not_called()
    else:
        migration_router.assert_called_once_with(using, addon_model)
        if allow_migrate:
            table_probe.assert_called_once_with(connection, "platform_addon")
        else:
            table_probe.assert_not_called()
