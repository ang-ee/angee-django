"""Runtime checks belong to the base app, including non-Angee models."""

from __future__ import annotations

import pytest
from django.apps import apps
from django.core import checks
from django.core.checks.registry import registry
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps

from angee.base.apps import BaseConfig
from angee.base.checks import check_ownership
from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.implementations import check_impl_registry
from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyModel, AppendOnlyQuerySet
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeModel
from angee.base.models.hierarchy import HierarchyMixin, HierarchyQuerySet
from angee.base.models.ownership import OwnerMixin
from angee.base.models.trash import TrashMixin, TrashQuerySet
from angee.hooks import check_hook, check_hooks
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
    """The shared validator covers every entry without a separate declaration list."""

    with override_settings(
        ANGEE_TEST_IMPLS={
            "missing": "tests.test_impl.MissingImpl",
            "wrong_base": "builtins.str",
            "wrong_key": "tests.test_impl._BaseImpl",
        },
    ):
        errors = check_impl_registry(_BaseImpl)
    assert [error.id for error in errors] == ["angee.E003", "angee.E004", "angee.E004"]
    assert "MissingImpl" in errors[0].msg
    assert "is not a _BaseImpl" in errors[1].msg
    assert "with key 'base'" in errors[2].msg


def test_impl_checks_need_no_meta_declaration_and_preserve_empty_rowless_catalogues() -> None:
    from tests.test_impl import _EmptyImpl

    with override_settings(ANGEE_EMPTY_IMPLS={}):
        assert check_impl_registry(_EmptyImpl) == []
    with override_settings(ANGEE_TEST_IMPLS={"base": "tests.test_impl._BaseImpl"}):
        assert ImplClassField(_BaseImpl).check() == []


def test_rowless_resource_check_validates_unused_entries_and_registers_once() -> None:
    from angee.resources.sources import check_source_registry

    config = apps.get_app_config("resources")
    config.ready()
    config.ready()
    assert sum(check is check_source_registry for check in registry.registered_checks) == 1
    with override_settings(ANGEE_RESOURCE_SOURCE_CLASSES={
        "path": "angee.resources.sources.PathSource", "unused": "builtins.MissingSource",
    }):
        errors = check_source_registry(app_configs=None)
    assert [error.id for error in errors] == ["angee.E003"]
    assert "MissingSource" in errors[0].msg


def test_hook_check_imports_every_declared_path() -> None:
    """A bad optional hook and each bad list entry fail at Django startup."""

    with override_settings(
        ANGEE_TEST_SINGLE_HOOK="builtins.MissingHook",
        ANGEE_TEST_HOOKS=["builtins.len", "builtins.Ellipsis", "builtins.MissingHook"],
    ):
        errors = [*check_hook("ANGEE_TEST_SINGLE_HOOK"), *check_hooks(("ANGEE_TEST_HOOKS",))]
    assert [error.id for error in errors] == ["angee.E033"] * 3
    assert "MissingHook" in errors[0].msg
    assert "not callable" in errors[1].msg
    assert "MissingHook" in errors[2].msg


def test_hook_check_accepts_unset_optional_hook_and_declared_list_order() -> None:
    with override_settings(
        ANGEE_TEST_SINGLE_HOOK="",
        ANGEE_TEST_HOOKS=["builtins.sorted", "builtins.len"],
    ):
        assert check_hook("ANGEE_TEST_SINGLE_HOOK") == []
        assert check_hooks(("ANGEE_TEST_HOOKS",)) == []


def test_single_hook_owner_checks_reject_multiple_valid_callables() -> None:
    from angee.mcp.apps import check_settings as mcp_settings
    from angee.messaging.apps import check_settings as messaging_settings

    for owner_check, name in (
        (mcp_settings, "ANGEE_MCP_ACTOR_VERIFIER"),
        (messaging_settings, "ANGEE_WEBFORM_TOKEN_HOOK"),
    ):
        with override_settings(**{name: ["builtins.len", "builtins.sorted"]}):
            [error] = owner_check()
        assert error.id == "angee.E033"
        assert "at most one" in error.msg


def test_installed_hook_owners_register_their_checks(monkeypatch) -> None:
    from angee.base.apps import check_settings as base_settings
    from angee.graphql.apps import GraphQLConfig
    from angee.graphql.apps import check_settings as graphql_settings
    from angee.mcp.apps import check_settings as mcp_settings
    from angee.messaging.apps import check_settings as messaging_settings
    from angee.parties.apps import check_settings as parties_settings
    from angee.timeline.apps import check_settings as timeline_settings

    # The source test host deliberately uses BareGraphQLConfig; exercise its
    # production ready hook explicitly without leaking check registrations.
    monkeypatch.setattr(registry, "registered_checks", registry.registered_checks.copy())
    GraphQLConfig("angee.graphql", apps.get_app_config("graphql").module).ready()
    for owner_check in (
        base_settings, graphql_settings, mcp_settings, messaging_settings, parties_settings, timeline_settings,
    ):
        assert owner_check in registry.registered_checks
        assert owner_check() == []


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


def test_native_rebac_database_check_and_base_checks_are_registered() -> None:
    """Native REBAC owns alias consistency; base registers only its own policies."""

    from rebac.checks import check_policy_models

    config = apps.get_app_config("base")
    assert isinstance(config, BaseConfig)
    assert config.name == "angee.base"
    config.ready()
    config.ready()
    assert check_policy_models in registry.registered_checks
    assert checks.Tags.database in check_policy_models.tags
    from angee.base.apps import check_settings

    assert sum(check is check_settings for check in registry.registered_checks) == 1
    assert checks.Tags.models in check_settings.tags


@pytest.mark.parametrize("ordering", ["first", "last", "override", "inherited", "plain"])
def test_hierarchy_queryset_order_checks_every_declared_manager(ordering: str) -> None:
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
    with isolate_apps("django.contrib.contenttypes"):

        class Node(HierarchyMixin, models.Model):
            objects = models.Manager()
            hierarchy = models.Manager.from_queryset(queryset)()

            class Meta:
                app_label = "contenttypes"

        errors = [error for error in Node.check() if error.id == "angee.E021"]

    if ordering in {"last", "override"}:
        [error] = errors
        assert error.id == "angee.E021"
        assert error.obj is Node
        assert "contenttypes.Node.hierarchy" in error.msg
    else:
        assert errors == []


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
        errors = [
            error for model in isolated.get_models() for error in model.check()
            if error.id == "angee.E027"
        ]

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
        assert not [error for model in isolated.get_models() for error in model.check() if error.id == "angee.E027"]


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
