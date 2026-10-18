"""Runtime checks belong to the base app, including non-Angee models."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core import checks
from django.core.checks.registry import registry
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps
from rebac import RebacMixin
from rebac.models import RebacResource, Relationship, RelationshipRegistry

from angee import checks as base_checks
from angee.apps import BaseConfig
from angee.checks import (
    check_hierarchy_queryset_order,
    check_hooks,
    check_impl_registries,
    check_ownership,
    check_rebac_database,
)
from angee.fields import StateField
from angee.impl import ImplClassField
from angee.models import (
    AngeeManager,
    AngeeModel,
    AppendOnlyModel,
    AppendOnlyQuerySet,
    AuditMixin,
    HierarchyQuerySet,
    OwnerMixin,
    TrashMixin,
    TrashQuerySet,
)
from tests.proposals_models import Round
from tests.test_impl import _BaseImpl


@pytest.mark.parametrize("guarded", [False, True])
def test_append_only_default_manager_check(guarded: bool) -> None:
    """Django's model checks catch a concrete retained row with an ordinary manager."""

    with isolate_apps("django.contrib.contenttypes") as isolated:
        class Retained(AppendOnlyModel):
            objects = AngeeManager.from_queryset(AppendOnlyQuerySet)() if guarded else AngeeManager()

            class Meta(AppendOnlyModel.Meta):
                app_label = "contenttypes"
                abstract = False

        errors = checks.run_checks(app_configs=list(isolated.get_app_configs()), tags=[checks.Tags.models])
        append_errors = [error for error in errors if error.id == "angee.E034"]
        assert len(append_errors) == (0 if guarded else 1)
        if append_errors:
            assert append_errors[0].obj is Retained


@pytest.mark.parametrize("guarded", [False, True])
def test_trash_default_manager_check(guarded: bool) -> None:
    """A model storing the trash flag must offer the trash row scopes."""

    with isolate_apps("django.contrib.contenttypes") as isolated:
        class Removable(TrashMixin):
            objects = TrashQuerySet.as_manager() if guarded else models.Manager()

            class Meta:
                app_label = "contenttypes"

        errors = checks.run_checks(app_configs=list(isolated.get_app_configs()), tags=[checks.Tags.models])
        trash_errors = [error for error in errors if error.id == "angee.E036"]
        assert len(trash_errors) == (0 if guarded else 1)
        if trash_errors:
            assert trash_errors[0].obj is Removable


def test_installed_trash_models_compose_the_trash_scopes() -> None:
    models_ = [model for model in apps.get_models() if issubclass(model, TrashMixin)]
    assert models_
    assert all(not [error for error in model.check() if error.id == "angee.E036"] for model in models_)


def test_installed_append_only_default_managers_compose_guard() -> None:
    models = [model for model in apps.get_models() if issubclass(model, AppendOnlyModel)]
    assert models
    assert all(not [error for error in model.check() if error.id == "angee.E034"] for model in models)


def test_impl_registry_check_covers_bad_paths_wrong_bases_and_keys() -> None:
    """The base check validates rowless catalogues without a model field."""

    with override_settings(
        ANGEE_IMPL_REGISTRIES=["tests.test_impl._BaseImpl", "tests.test_impl.MissingBase"],
        ANGEE_TEST_IMPLS={
            "missing": "tests.test_impl.MissingImpl",
            "wrong_base": "builtins.str",
            "wrong_key": "tests.test_impl._BaseImpl",
        },
    ):
        errors = check_impl_registries()
    assert [error.id for error in errors] == ["angee.E003", "angee.E004", "angee.E004", "angee.E031"]
    assert "MissingImpl" in errors[0].msg
    assert "is not a _BaseImpl" in errors[1].msg
    assert "with key 'base'" in errors[2].msg
    assert "MissingBase" in errors[3].msg


def test_impl_registry_check_accepts_empty_catalogue_and_rejects_unlisted_field() -> None:
    """An empty rowless registry is valid, while model selectors must be declared."""

    with override_settings(ANGEE_IMPL_REGISTRIES=["tests.test_impl._EmptyImpl"], ANGEE_EMPTY_IMPLS={}):
        assert check_impl_registries() == []
    with override_settings(ANGEE_IMPL_REGISTRIES=[], ANGEE_TEST_IMPLS={"base": "tests.test_impl._BaseImpl"}):
        [error] = ImplClassField(_BaseImpl).check()
    assert error.id == "angee.E032"


def test_hook_check_imports_every_declared_path() -> None:
    """A bad optional hook and each bad list entry fail at Django startup."""

    with override_settings(
        ANGEE_HOOKS=["ANGEE_TEST_SINGLE_HOOK", "ANGEE_TEST_HOOKS"],
        ANGEE_TEST_SINGLE_HOOK="builtins.MissingHook",
        ANGEE_TEST_HOOKS=["builtins.len", "builtins.Ellipsis", "builtins.MissingHook"],
    ):
        errors = check_hooks()
    assert [error.id for error in errors] == ["angee.E033"] * 3
    assert "MissingHook" in errors[0].msg
    assert "not callable" in errors[1].msg
    assert "MissingHook" in errors[2].msg


def test_hook_check_accepts_unset_optional_hook_and_declared_list_order() -> None:
    with override_settings(
        ANGEE_HOOKS=["ANGEE_TEST_SINGLE_HOOK", "ANGEE_TEST_HOOKS"],
        ANGEE_TEST_SINGLE_HOOK="",
        ANGEE_TEST_HOOKS=["builtins.sorted", "builtins.len"],
    ):
        assert check_hooks() == []


def test_installed_hook_owners_declare_their_settings() -> None:
    assert {
        "ANGEE_MCP_ACTOR_VERIFIER",
        "ANGEE_WEBFORM_TOKEN_HOOK",
        "ANGEE_TASK_LOCK_BACKEND",
        "ANGEE_PARTIES_SHARED_SENDER_PROVIDERS",
        "ANGEE_PARTIES_SIGNING_PROVIDERS",
        "ANGEE_MERGE_GUARDS",
    } <= set(settings.ANGEE_HOOKS)
    assert check_hooks() == []


@pytest.mark.parametrize("value", ("names", "values", "labels", "choices"))
def test_state_field_rejects_values_that_cannot_be_reconstructed(value: str) -> None:
    """Migration-state cloning must not generate a reserved enum member name."""
    enum = models.TextChoices("ReservedValue", {"MEMBER": (value, "Label")})
    field = StateField(choices_enum=enum)
    assert any(error.id == "angee.E028" and value in error.msg for error in field.check())


def test_named_roster_state_survives_historical_reconstruction() -> None:
    field = Round._meta.get_field("roster_visibility")
    assert field.clone().choices == field.choices
    assert "named" in field.choices_enum.values
    assert not field.check()


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
    assert config.name == "angee"
    config.ready()
    config.ready()
    assert sum(check is check_rebac_database for check in registry.registered_checks) == 1
    assert checks.Tags.models in check_rebac_database.tags
    assert sum(check is check_hierarchy_queryset_order for check in registry.registered_checks) == 1
    assert checks.Tags.models in check_hierarchy_queryset_order.tags
    assert sum(check is check_hooks for check in registry.registered_checks) == 1
    assert checks.Tags.models in check_hooks.tags


@pytest.mark.parametrize("ordering", ["first", "last", "override", "inherited", "plain"])
@pytest.mark.parametrize("selected_apps", [False, True])
def test_hierarchy_queryset_order_checks_every_declared_manager(
    monkeypatch: pytest.MonkeyPatch,
    ordering: str,
    selected_apps: bool,
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


@pytest.mark.parametrize("mutation", ["valid", "missing", "wrong", "widened", "stored-parent", "wrong-parent"])
def test_inherited_owner_gate_uses_the_concrete_parents_transfer_permission(tmp_path, monkeypatch, mutation):
    with isolate_apps("django.contrib.contenttypes") as isolated:
        class OwnedParent(OwnerMixin, AngeeModel):
            owner_transfer_permission = "reassign"

            class Meta:
                app_label = "contenttypes"
                rebac_resource_type = "tests/owned_parent"

        class OwnedChild(OwnedParent):
            owner_transfer_permission = "unused_child_transfer"

            class Meta:
                app_label = "contenttypes"
                rebac_resource_type = "tests/owned_child"

        source = """
definition tests/owned_parent {
    relation owner: auth/user // rebac:field=owner
    permission reassign = owner
    permission write__owner = reassign
}
definition tests/owned_child {
    relation parent: tests/owned_parent // rebac:field=ownedparent_ptr
    permission write__owner = parent->reassign
}
"""
        if mutation == "missing":
            source = source.replace("permission write__owner = parent->reassign", "")
        elif mutation == "wrong":
            source = source.replace("parent->reassign", "parent->write")
        elif mutation == "widened":
            source = source.replace("parent->reassign", "parent->reassign + authenticated")
        elif mutation == "stored-parent":
            source = source.replace(" // rebac:field=ownedparent_ptr", "")
        elif mutation == "wrong-parent":
            source = source.replace("relation parent: tests/owned_parent", "relation parent: tests/other_parent")
        path = tmp_path / "ownership.zed"
        path.write_text(source)
        monkeypatch.setattr(apps.get_app_config("contenttypes"), "rebac_schema", str(path), raising=False)
        errors = check_ownership([isolated.get_app_config("contenttypes")])

    if mutation == "valid":
        assert errors == []
    else:
        [error] = errors
        assert error.id == "angee.E027" and error.obj is OwnedChild
        assert "contenttypes.OwnedChild" in error.msg and "tests/owned_child" in error.msg
        assert "->reassign" in error.msg


def test_a_child_sharing_its_owners_definition_takes_its_transfer_gate(tmp_path, monkeypatch):
    with isolate_apps("django.contrib.contenttypes") as isolated:
        class SharedParent(OwnerMixin, AngeeModel):
            class Meta:
                app_label = "contenttypes"
                rebac_resource_type = "tests/shared_parent"

        class SharedChild(SharedParent):
            class Meta:
                app_label = "contenttypes"
                rebac_resource_type = "tests/shared_parent"

        path = tmp_path / "ownership.zed"
        path.write_text(
            "definition tests/shared_parent {\n    relation owner: auth/user // rebac:field=owner\n"
            "    permission transfer = owner\n    permission write__owner = transfer\n}\n"
        )
        monkeypatch.setattr(apps.get_app_config("contenttypes"), "rebac_schema", str(path), raising=False)
        assert check_ownership([isolated.get_app_config("contenttypes")]) == []


@pytest.mark.parametrize(
    ("owned", "relation", "flagged"),
    [
        (True, "relation owner: auth/user // rebac:field=owner", False),
        (True, "relation owner: auth/user // rebac:field=created_by", True),
        (False, "relation owner: auth/user // rebac:field=created_by", True),
        (False, "relation owner: auth/user", True),
        (False, "relation creator: auth/user // rebac:field=created_by", False),
    ],
)
def test_owner_names_only_the_owner_mixin_column(tmp_path, monkeypatch, owned, relation, flagged):
    """A relation named owner is OwnerMixin's column; attribution and roles take their own words."""

    with isolate_apps("django.contrib.contenttypes") as isolated:
        if owned:

            class OwnedRow(OwnerMixin, AngeeModel):
                class Meta:
                    app_label = "contenttypes"
                    rebac_resource_type = "tests/row"

            row = OwnedRow
        else:

            class AttributedRow(AuditMixin, AngeeModel):
                class Meta:
                    app_label = "contenttypes"
                    rebac_resource_type = "tests/row"

            row = AttributedRow
        name = relation.split()[1].rstrip(":")
        path = tmp_path / "ownership.zed"
        path.write_text(
            f"definition tests/row {{\n    {relation}\n    permission transfer = {name}\n"
            "    permission write__owner = transfer\n}\n"
        )
        monkeypatch.setattr(apps.get_app_config("contenttypes"), "rebac_schema", str(path), raising=False)
        errors = check_ownership([isolated.get_app_config("contenttypes")])

    spelling = [error for error in errors if error.id == "angee.E045"]
    if flagged:
        [error] = spelling
        assert error.obj is row and "tests/row" in error.msg
    else:
        assert errors == []


@pytest.mark.django_db
def test_installed_models_spell_ownership_one_way() -> None:
    """No installed definition names a relation owner other than OwnerMixin's column.

    Test modules register their own models in the shared registry, so this
    asserts the spelling rule alone; E022 and E027 have their focused tests.
    """

    assert [error.msg for error in check_ownership() if error.id == "angee.E045"] == []
