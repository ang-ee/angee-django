"""Runtime checks belong to the base app, including non-Angee models."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps
from django.core import checks
from django.core.checks.registry import registry
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test import override_settings
from django.test.utils import isolate_apps

from angee.base.apps import BaseConfig
from angee.base.build import extension_source_map
from angee.base.checks import (
    check_database_routers,
    check_ownership,
    check_public_origin,
    check_sealed_permissions,
    check_stored_impl_keys,
)
from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.hooks import check_hook, check_hooks
from angee.base.implementations import check_impl_registry
from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyModel, AppendOnlyQuerySet
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeModel
from angee.base.models.hierarchy import HierarchyMixin, HierarchyQuerySet
from angee.base.models.ownership import OwnerMixin
from angee.base.models.trash import TrashMixin, TrashQuerySet
from angee.base.origins import PublicOriginUnavailable, configured_public_origin
from tests.proposals_models import Round
from tests.test_impl import _BaseImpl
from tests.test_zed_extensions import _addon, _apply_emitted_schema_paths, _contrib_addon


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
    assert check_hook("ANGEE_TEST_UNSET_HOOK") == []
    assert check_hooks(("ANGEE_TEST_UNSET_HOOK",)) == []
    with override_settings(
        ANGEE_TEST_SINGLE_HOOK="",
        ANGEE_TEST_HOOKS=["builtins.sorted", "builtins.len"],
    ):
        assert check_hook("ANGEE_TEST_SINGLE_HOOK") == []
        assert check_hooks(("ANGEE_TEST_HOOKS",)) == []


def test_single_hook_owner_checks_reject_multiple_valid_callables() -> None:
    from angee.mcp.apps import check_settings as mcp_settings
    from angee.webforms.apps import check_settings as webforms_settings

    for owner_check, name in (
        (mcp_settings, "ANGEE_MCP_ACTOR_VERIFIER"),
        (webforms_settings, "ANGEE_WEBFORM_TOKEN_HOOK"),
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
    from angee.parties.apps import check_settings as parties_settings
    from angee.timeline.apps import check_settings as timeline_settings
    from angee.webforms.apps import check_settings as webforms_settings

    # The source test host deliberately uses BareGraphQLConfig; exercise its
    # production ready hook explicitly without leaking check registrations.
    monkeypatch.setattr(registry, "registered_checks", registry.registered_checks.copy())
    GraphQLConfig("angee.graphql", apps.get_app_config("graphql").module).ready()
    for owner_check in (
        base_settings, graphql_settings, mcp_settings, parties_settings, timeline_settings, webforms_settings,
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
    assert sum(check is check_database_routers for check in registry.registered_checks) == 1


ORIGINS = [
    ("", False, True),
    ("https://app.example.test", False, True),
    ("https://app.example.test:8443/", False, True),
    ("http://localhost:5173", True, True),
    ("http://app.example.test", False, False),
    ("https://*.example.test", False, False),
    ("https://user:secret@app.example.test", False, False),
    ("https://app.example.test/path", False, False),
    ("https://app.example.test:notaport", False, False),
    ("https://exa mple.test", False, False),
]


@pytest.mark.parametrize(("origin", "debug", "valid"), ORIGINS)
def test_the_configured_public_origin_is_the_one_the_check_accepts_and_none_is_a_coded_refusal(
    origin: str, debug: bool, valid: bool,
) -> None:
    """The accessor asks the owner's rule of the setting: a valid origin comes back bare; an unset or invalid one is
    refused with the owner's code, as an ``ImproperlyConfigured`` too."""

    with override_settings(ANGEE_PUBLIC_ORIGIN=origin, DEBUG=debug):
        if origin and valid:
            assert configured_public_origin() == origin.rstrip("/")
            return
        with pytest.raises(PublicOriginUnavailable) as refusal:
            configured_public_origin()
    assert (refusal.value.code, str(refusal.value)) == ("PUBLIC_ORIGIN_UNAVAILABLE", "PUBLIC_ORIGIN_UNAVAILABLE")
    assert isinstance(refusal.value, ImproperlyConfigured)


@pytest.mark.parametrize(("origin", "debug", "valid"), ORIGINS)
def test_an_invalid_public_origin_fails_the_system_check(origin: str, debug: bool, valid: bool) -> None:
    """Only a bare public origin is trusted for CSRF and links; any other set value is reported."""

    assert sum(check is check_public_origin for check in registry.registered_checks) == 1
    with override_settings(ANGEE_PUBLIC_ORIGIN=origin, DEBUG=debug):
        errors = check_public_origin()
    assert [error.id for error in errors] == ([] if valid else ["angee.E051"])


def test_a_database_router_is_refused() -> None:
    """Angee runs on one database: any configured router fails the system check."""

    class ReplicaRouter:
        """A router that would move reads off the default database."""

    assert check_database_routers() == []
    with override_settings(DATABASE_ROUTERS=[ReplicaRouter()]):
        [error] = check_database_routers()
    assert error.id == "angee.E050"
    assert "DATABASE_ROUTERS" in error.msg


@pytest.mark.parametrize("aliases", [(), ("other",), ("default", "other")])
def test_database_configuration_requires_only_default(settings, aliases) -> None:
    settings.DATABASES = {alias: {"ENGINE": "django.db.backends.sqlite3"} for alias in aliases}
    [error] = check_database_routers()
    assert error.id == "angee.E050"
    assert "DATABASES" in error.msg


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


def test_a_seal_names_an_installed_owner_and_its_declared_permissions() -> None:
    """A seal without its owner or on an undeclared permission is reported, never raised."""

    [missing_owner] = check_sealed_permissions("absent", {"absent/thing": ("read",)}, "tests.E001", "Unused.")
    assert missing_owner.id == "tests.E001"
    assert "absent declares no permissions schema" in missing_owner.msg
    [undeclared] = check_sealed_permissions(
        "iam", {"auth/user": ("view_as", "impersonate")}, "tests.E001", "Unused.",
    )
    assert undeclared.id == "tests.E001"
    assert "auth/user#impersonate" in undeclared.msg
    assert "auth/user#view_as" not in undeclared.msg
    assert check_sealed_permissions("iam", {"auth/user": ("view_as",)}, "tests.E001", "Unused.") == []


def test_each_seal_refuses_a_fragment_with_its_own_remedy(tmp_path: Path) -> None:
    """A fragment that changes two sealed permissions meets each seal's own message and hint."""

    declaration = Path(apps.get_app_config("iam").path, "permissions.zed").read_text(encoding="utf-8")
    iam = _addon(tmp_path, "iam", "permissions.zed", declaration)
    contrib = _contrib_addon(
        tmp_path,
        "definition auth/user {\n    relation helper: auth/user\n"
        "    permission view_as = helper\n    permission set_password = helper\n}\n",
        name="seal_contrib",
    )
    sources = extension_source_map([iam, contrib])
    runtime = tmp_path / "runtime"
    for relative, text in sources.items():
        output = runtime / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    _apply_emitted_schema_paths([iam, contrib], runtime, sources=sources)

    seals = {
        "auth/user#view_as": ("tests.E002", "Grant preview through the fragment's own record."),
        "auth/user#set_password": ("tests.E003", "Leave Set password to its two write gates."),
    }
    for sealed, (check_id, hint) in seals.items():
        resource_type, _, name = sealed.partition("#")
        [error] = check_sealed_permissions("iam", {resource_type: (name,)}, check_id, hint, [iam, contrib])
        assert (error.id, error.hint) == (check_id, hint)
        assert error.msg == f"iam: {sealed} differ from angee.iam's declaration (extended by seal_contrib@0)."


@pytest.mark.django_db
@override_settings(ANGEE_TEST_IMPLS={"base": "tests.test_impl._BaseImpl"})
def test_a_stored_implementation_key_nothing_registers_is_named_with_its_rows_and_its_remedy() -> None:
    """Rows stranded by a key that left its registry are found by the database check, never a plain one."""

    with isolate_apps("django.contrib.contenttypes") as isolated:
        class Chosen(models.Model):
            impl = ImplClassField(_BaseImpl, default="base")

            class Meta:
                app_label = "contenttypes"
                db_table = "base_checks_chosen"

        selected = list(isolated.get_app_configs())

        def found() -> list[checks.CheckMessage]:
            return check_stored_impl_keys(app_configs=selected, databases=["default"])

        assert found() == [], "a table the pending migrations have yet to make is passed over"
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE base_checks_chosen (id integer PRIMARY KEY, impl varchar(64))")
            cursor.executemany(
                "INSERT INTO base_checks_chosen (id, impl) VALUES (%s, %s)", [(1, "base"), (2, "gone"), (3, "gone")],
            )

        assert check_stored_impl_keys(app_configs=selected) == [], "a plain check reads no database"
        (error,) = found()
        assert (error.id, error.obj) == ("angee.E052", Chosen)
        assert all(part in error.msg for part in ("contenttypes.Chosen.impl", "2 row(s)", "'gone'", "ANGEE_TEST_IMPLS"))
        with override_settings(ANGEE_IMPL_KEY_REMEDIES={"ANGEE_TEST_IMPLS": {"gone": "Install it."}}):
            assert found()[0].hint == "Install it."
        assert check_stored_impl_keys(app_configs=[], databases=["default"]) == [], "only the selected apps are read"

        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE base_checks_chosen RENAME COLUMN impl TO renamed")
        assert found() == [], "a column the pending migrations have yet to add is passed over"

    assert check_stored_impl_keys(databases=["default"]) == [], "every installed model's keys are registered"
    assert check_stored_impl_keys in registry.get_checks()
    assert check_stored_impl_keys.tags == (checks.Tags.database,)
