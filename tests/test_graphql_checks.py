"""Focused checks for GraphQL composition validation."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps
from rebac import RebacMixin

from angee.graphql import checks as graphql_checks
from angee.graphql.checks import check_graphql_schemas, check_rebac_database
from angee.graphql.schema import GraphQLSchemas


def test_graphql_check_reports_discovery_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken addon declaration becomes an actionable Django check error."""

    def fail_discovery(cls: type[GraphQLSchemas]) -> Any:
        del cls
        raise RuntimeError("invalid addon schema reference")

    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(fail_discovery))

    [error] = check_graphql_schemas()

    assert error.id == "angee.graphql.E001"
    assert error.msg == "GraphQL schema discovery failed: invalid addon schema reference"
    assert error.hint == "Fix the owning addon manifest or schema declaration, then rerun manage.py check."


@pytest.mark.parametrize("write_alias", [None, "default", "external"])
@pytest.mark.parametrize("selected_apps", [False, True])
def test_rebac_check_requires_default_write_routing(
    monkeypatch: pytest.MonkeyPatch,
    write_alias: str | None,
    selected_apps: bool,
) -> None:
    """The native router determines writes; unrelated models and reads are ignored."""

    with isolate_apps("django.contrib.contenttypes") as registry:
        class Managed(RebacMixin):
            class Meta:
                app_label = "contenttypes"
                rebac_resource_type = "tests/routing-check"

        class Unmanaged(models.Model):
            class Meta:
                app_label = "contenttypes"

        class Router:
            def db_for_write(self, model: type[models.Model], **hints: Any) -> str | None:
                return write_alias if model is Managed else "external"

            def db_for_read(self, model: type[models.Model], **hints: Any) -> str:
                return "replica"

        monkeypatch.setattr(graphql_checks, "apps", registry)
        app_configs = list(registry.get_app_configs()) if selected_apps else None
        with override_settings(DATABASE_ROUTERS=[Router()]):
            errors = check_rebac_database(app_configs)

        if write_alias in (None, "default"):
            assert errors == []
        else:
            [error] = errors
            assert error.id == "angee.rebac.E001"
            assert error.obj is Managed
            assert error.msg == (
                "REBAC-managed model contenttypes.Managed routes writes to 'external'; "
                "REBAC requires the default database."
            )
            assert error.hint == "Update DATABASE_ROUTERS so REBAC-managed models write to 'default'."
