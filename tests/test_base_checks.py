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
from angee.base.checks import check_hierarchy_queryset_order, check_rebac_database
from angee.base.mixins import HierarchyQuerySet


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
    assert sum(check is check_hierarchy_queryset_order for check in registry.registered_checks) == 1
    assert checks.Tags.models in check_hierarchy_queryset_order.tags


@pytest.mark.parametrize("ordering", ["first", "last", "override", "inherited", "plain"])
@pytest.mark.parametrize("selected_apps", [False, True])
def test_hierarchy_queryset_order_checks_every_declared_manager(
    monkeypatch: pytest.MonkeyPatch, ordering: str, selected_apps: bool,
) -> None:
    """Path maintenance may skip its own guard, but must retain other update guards."""

    class Guard(models.QuerySet):
        def update(self, **kwargs):
            raise AssertionError("The other owner's guard must remain reachable.")

    class First(HierarchyQuerySet, Guard):
        pass

    class Last(Guard, HierarchyQuerySet):
        pass

    class Override(First):
        def update(self, **kwargs):
            return super().update(**kwargs)

    class Inherited(First):
        pass

    queryset = {"first": First, "last": Last, "override": Override, "inherited": Inherited, "plain": Guard}[ordering]
    with isolate_apps("django.contrib.contenttypes") as registry:
        class Node(models.Model):
            objects = models.Manager()
            hierarchy = models.Manager.from_queryset(queryset)()

            class Meta:
                app_label = "contenttypes"

        monkeypatch.setattr(base_checks, "apps", registry)
        configs = list(registry.get_app_configs()) if selected_apps else None
        errors = check_hierarchy_queryset_order(configs)

    if ordering in {"last", "override"}:
        [error] = errors
        assert error.id == "angee.E021"
        assert error.obj is Node
        assert "contenttypes.Node.hierarchy" in error.msg
    else:
        assert errors == []


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
