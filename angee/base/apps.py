"""Django lifecycle for shared model-foundation enforcement."""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig
from django.core import checks
from django.db.backends.signals import connection_created
from django.db.models.signals import m2m_changed


def _register_import_scope(sender: Any, connection: Any, **kwargs: Any) -> None:
    from angee.base.ownership import register_sqlite_import_scope

    register_sqlite_import_scope(connection)


class BaseConfig(AppConfig):
    """Install model guards without requiring an integration addon."""

    default = True
    name = "angee.base"

    def ready(self) -> None:
        """Bind enforcement after Django has populated its model registry."""

        from angee.base.importing import (
            check_external_ownership_declarations,
            check_external_ownership_relation,
        )

        super().ready()
        connection_created.connect(
            _register_import_scope,
            dispatch_uid="angee.base.sqlite_import_scope",
        )
        m2m_changed.connect(
            check_external_ownership_relation,
            dispatch_uid="angee.base.external_ownership_relations",
        )
        checks.register(checks.Tags.models)(check_external_ownership_declarations)
