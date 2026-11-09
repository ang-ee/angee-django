"""The native pre-migrate lifecycle prepares pgvector before host table DDL."""

from unittest.mock import MagicMock

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connection
from django.db.models.signals import pre_migrate

from angee.knowledge_graph_pgvector.signals import prepare_vector_extension


def test_pre_migrate_prepares_vector_once_for_the_addon(monkeypatch):
    monkeypatch.setattr(connection, "vendor", "postgresql")
    cursor = MagicMock()
    monkeypatch.setattr(connection, "cursor", lambda: cursor)
    pre_migrate.send(sender=apps.get_app_config("knowledge_graph_pgvector"), using="default")
    cursor.__enter__.return_value.execute.assert_called_once_with("CREATE EXTENSION IF NOT EXISTS vector")


def test_pre_migrate_is_a_noop_on_sqlite(monkeypatch):
    monkeypatch.setattr(connection, "vendor", "sqlite")
    cursor = MagicMock(side_effect=AssertionError("SQLite does not create PostgreSQL extensions"))
    monkeypatch.setattr(connection, "cursor", cursor)
    prepare_vector_extension(None, using="default")
    cursor.assert_not_called()


def test_pre_migrate_reports_extension_installation_and_privilege_failure(monkeypatch):
    monkeypatch.setattr(connection, "vendor", "postgresql")
    cursor = MagicMock()
    cursor.__enter__.return_value.execute.side_effect = DatabaseError("permission denied")
    monkeypatch.setattr(connection, "cursor", lambda: cursor)
    with pytest.raises(ImproperlyConfigured, match="vector.*privileges"):
        prepare_vector_extension(None, using="default")
