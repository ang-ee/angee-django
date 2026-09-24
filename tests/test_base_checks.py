"""Runtime persistence checks belong to the base app, including non-Angee models."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.core import checks
from django.core.checks.registry import registry
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps
from rebac import RebacMixin
from rebac.models import RebacResource, Relationship, RelationshipRegistry

from angee.base import checks as base_checks
from angee.base.apps import BaseConfig
from angee.base.checks import check_rebac_database


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

        monkeypatch.setattr(base_checks, "apps", registry)
        app_configs = list(registry.get_app_configs()) if selected_apps else None
        with override_settings(DATABASE_ROUTERS=[Router()]):
            errors = check_rebac_database(app_configs)

        if write_alias in (None, "default"):
            assert errors == []
        else:
            [error] = errors
            assert error.id == "angee.E020"
            assert error.obj is Managed
            assert error.msg == (
                "REBAC-managed model contenttypes.Managed routes writes to 'external'; "
                "REBAC requires the default database."
            )
            assert error.hint == "Update DATABASE_ROUTERS so REBAC-managed models write to 'default'."


def test_rebac_database_check_registered_by_base() -> None:
    """The base app registers the contract even when GraphQL ready hooks are absent."""

    config = apps.get_app_config("base")
    assert isinstance(config, BaseConfig)
    config.ready()
    config.ready()
    assert sum(check is check_rebac_database for check in registry.registered_checks) == 1
    assert checks.Tags.models in check_rebac_database.tags


@pytest.mark.parametrize("store_model", [Relationship, RelationshipRegistry, RebacResource])
def test_rebac_database_check_covers_relationship_and_resource_stores(store_model) -> None:
    """A store without a resource type still participates in REBAC persistence."""

    class Router:
        def db_for_write(self, model: type[models.Model], **hints: Any) -> str | None:
            return "external" if model is store_model else None

    with override_settings(DATABASE_ROUTERS=[Router()]):
        [error] = check_rebac_database()

    assert error.id == "angee.E020"
    assert error.obj is store_model
