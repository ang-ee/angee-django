from unittest.mock import MagicMock

from angee.parties.runtime_migrations.address_one_primary import normalize_primaries


def test_primary_backfill_clears_historical_model_ordering() -> None:
    manager = MagicMock()
    filtered = manager.filter.return_value
    unordered = filtered.order_by.return_value
    unordered.values_list.return_value.distinct.return_value.iterator.return_value = iter(())
    address_model = MagicMock()
    address_model._base_manager.using.return_value = manager
    apps = MagicMock()
    apps.get_model.return_value = address_model
    editor = MagicMock()
    editor.connection.alias = "default"

    normalize_primaries(apps, editor)

    filtered.order_by.assert_called_once_with()
