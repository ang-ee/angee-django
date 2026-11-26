"""Tests for the framework ImplBase: default inheritance, choice metadata, materialise."""

from __future__ import annotations

import importlib.util
import os
import pickle
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

import pytest
from django.apps import apps
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.db.migrations.state import ProjectState
from django.db.models import Case, F, Value, When
from django.test import override_settings
from django.test.utils import isolate_apps
from pydantic import AliasChoices, AnyUrl, BaseModel, ConfigDict, Field, PlainSerializer, create_model, field_validator
from rebac import actor_context, system_context

from angee.base.fields.implementation import ImplClassField, impl_choices_enum
from angee.base.hooks import resolve_hook, resolve_hooks
from angee.base.implementations import (
    ImplBase,
    ImplChoice,
    check_impl_registry,
    impl_choices,
    resolve_all_impl_classes,
    resolve_impl_class,
)
from angee.base.models.implementations import ImplDefaultsMixin
from angee.base.schemas.forms import config_form_spec
from angee.integrate_github.backend import GitHubBackend
from angee.integrate_vcs.backend import LocalVCSBackend
from angee.provenance.context import changeset
from angee.provenance.querysets import model_history
from angee.storage.backends import LocalBackend, StorageBackend
from tests.conftest import OAuthClient, VcsBridge, Vendor, create_user, make_integration, vault_for
from tests.tables import model_tables, store_column


class _KeyedStorageBackend(StorageBackend):
    key = "declared"


class _BaseImpl(ImplBase):
    registry_setting = "ANGEE_TEST_IMPLS"
    key = "base"
    label = "Base"
    category = "demo"
    defaults = {
        "authorize_endpoint": "https://base/authorize",
        "token_endpoint": "https://base/token",
    }


class _RefinedImpl(_BaseImpl):
    key = "refined"
    defaults = {
        "token_endpoint": "https://refined/token",
        "userinfo_endpoint": "https://refined/userinfo",
    }


class _BoolImpl(ImplBase):
    key = "boolish"
    label = "Boolish"
    defaults = {"login_enabled": True}


class _ConfigBase(ImplBase):
    key = "cfg_base"
    defaults = {"authorize_params": {"host": "base", "port": 1}}


class _ConfigRefined(_ConfigBase):
    key = "cfg_refined"
    defaults = {"authorize_params": {"port": 2, "tls": True}}


class _ScalarConfig(BaseModel):
    endpoint: str = Field(default="https://example.test", description="Service endpoint.")
    retries: int


class _TypedConfigImpl(_BaseImpl):
    key = "typed"
    config_model = _ScalarConfig


class _EmptyImpl(ImplBase):
    registry_setting = "ANGEE_EMPTY_IMPLS"


class _EnumImpl(_BaseImpl):
    registry_setting = "ANGEE_ENUM_TEST_CLASSES"


class _EnumRefinedImpl(_EnumImpl):
    key = "refined"


class _UnsupportedConfigImpl(_BaseImpl):
    key = "unsupported"

    class Config(BaseModel):
        headers: dict[str, str]

    config_model = Config


def test_impl_owner_public_import_contract() -> None:
    """Implementation fields, registry contracts and row defaults expose explicit owners."""

    from angee.base.fields.implementation import ImplClassField
    from angee.base.implementations import (
        ImplBase,
        ImplChoice,
        impl_registry,
        resolve_all_impl_classes,
        resolve_impl_class,
    )
    from angee.base.models.implementations import ImplDefaultsMixin

    assert ImplBase.__name__ == "ImplBase"
    assert ImplChoice.__name__ == "ImplChoice"
    assert ImplClassField.__name__ == "ImplClassField"
    assert ImplDefaultsMixin.__name__ == "ImplDefaultsMixin"
    assert callable(impl_registry)
    assert callable(resolve_all_impl_classes)
    assert callable(resolve_hook)
    assert callable(resolve_hooks)
    assert callable(resolve_impl_class)
    legacy_modules = ("impl_types", "registry")
    for legacy_module in legacy_modules:
        assert importlib.util.find_spec(f"angee.{legacy_module}") is None


def test_hooks_resolve_optional_single_and_declared_list_order() -> None:
    """Hook paths share callable validation without imposing registry keys."""

    with override_settings(ANGEE_TEST_HOOK=""):
        assert resolve_hook("ANGEE_TEST_HOOK") is None
    with override_settings(ANGEE_TEST_HOOK="builtins.len"):
        assert resolve_hook("ANGEE_TEST_HOOK") is len
    with override_settings(ANGEE_TEST_HOOKS=["builtins.sorted", "builtins.len", "builtins.sorted"]):
        assert resolve_hooks("ANGEE_TEST_HOOKS") == (sorted, len, sorted)


@pytest.mark.parametrize("path", ["builtins.MissingHook", "builtins.Ellipsis"])
def test_hooks_reject_unimportable_and_noncallable_paths(path: str) -> None:
    with (
        override_settings(ANGEE_TEST_HOOK=path),
        pytest.raises(ImproperlyConfigured, match="settings.ANGEE_TEST_HOOK hook"),
    ):
        resolve_hook("ANGEE_TEST_HOOK")


@override_settings(
    ANGEE_TEST_IMPLS={
        "refined": "tests.test_impl._RefinedImpl",
        "base": "tests.test_impl._BaseImpl",
    }
)
def test_resolve_all_impl_classes_is_sorted_and_validates_keys() -> None:
    """Registry enumeration is deterministic and owns declaration-key agreement."""

    assert resolve_all_impl_classes(_BaseImpl) == (
        _BaseImpl,
        _RefinedImpl,
    )

    with (
        override_settings(ANGEE_TEST_IMPLS={"wrong": "tests.test_impl._RefinedImpl"}),
        pytest.raises(ImproperlyConfigured, match="with key 'refined'"),
    ):
        resolve_all_impl_classes(_BaseImpl)
    with (
        override_settings(ANGEE_TEST_IMPLS={"wrong": "tests.test_impl._RefinedImpl"}),
        pytest.raises(ImproperlyConfigured, match="with key 'refined'"),
    ):
        resolve_impl_class(_BaseImpl, "wrong")


@override_settings(
    ANGEE_TEST_IMPLS={
        "base": "tests.test_impl._BaseImpl",
        "missing": "tests.test_impl.MissingImpl",
        "wrong_base": "builtins.str",
        "wrong_key": "tests.test_impl._RefinedImpl",
    }
)
def test_impl_registry_and_field_collect_every_registry_fault() -> None:
    """Field checks enforce the registry owner's full declaration contract."""

    faults: list[Exception] = []

    assert resolve_all_impl_classes(
        _BaseImpl,
        on_error=faults.append,
    ) == (_BaseImpl,)
    assert len(faults) == 3
    assert isinstance(faults[0], ImportError)
    assert isinstance(faults[1], ImproperlyConfigured)
    assert isinstance(faults[2], ImproperlyConfigured)

    field = ImplClassField(_BaseImpl)
    errors = field.check()
    assert [error.id for error in errors] == ["angee.E003", "angee.E004", "angee.E004"]
    assert all(error.obj is field for error in errors)
    assert "MissingImpl" in errors[0].msg
    assert "is not a _BaseImpl" in errors[1].msg
    assert "with key 'refined'" in errors[2].msg


@override_settings(ANGEE_TEST_IMPLS={"unsupported": "tests.test_impl._UnsupportedConfigImpl"})
def test_rowless_registry_checks_config_form_declarations() -> None:
    """A registry needs the same form declaration checks with or without a column."""

    errors = check_impl_registry(_BaseImpl)

    assert [error.id for error in errors] == ["angee.E005"]
    assert "config.headers" in errors[0].msg
    field = ImplClassField(_BaseImpl)
    assert [error.id for error in field.check()] == ["angee.E005"]


@override_settings(ANGEE_TEST_IMPLS=["not a registry"])
def test_registry_checks_report_a_malformed_mapping() -> None:
    """System checks report an invalid registry container without raising."""

    errors = check_impl_registry(_BaseImpl)

    assert [error.id for error in errors] == ["angee.E002"]
    assert "must be a mapping" in errors[0].msg


@override_settings(ANGEE_EMPTY_IMPLS={})
def test_empty_rowless_registry_is_valid_but_cannot_project_an_enum() -> None:
    """Empty catalogues need no artificial implementation to pass checks or list choices."""

    assert check_impl_registry(_EmptyImpl) == []
    assert impl_choices(_EmptyImpl) == []
    with pytest.raises(ImproperlyConfigured, match="registry .* is empty"):
        impl_choices_enum(_EmptyImpl)


@override_settings(
    ANGEE_TEST_IMPLS={
        "typed": "tests.test_impl._TypedConfigImpl",
        "base": "tests.test_impl._BaseImpl",
    }
)
def test_rowless_choices_share_field_metadata_and_order() -> None:
    """Registry choices retain implementation labels, defaults and config forms."""

    field = ImplClassField(_BaseImpl)
    choices = impl_choices(_BaseImpl)

    assert tuple(choice.key for choice in choices) == field.registered_keys()
    assert choices == [_BaseImpl.choice(), _TypedConfigImpl.choice()]


@override_settings(ANGEE_ENUM_TEST_CLASSES={"base": "tests.test_impl._EnumImpl"})
def test_registry_enum_identity_tracks_keys_and_preserves_names() -> None:
    """Native enum identity depends on the composed keys, not import paths or input order."""

    enum = impl_choices_enum(_EnumImpl)
    field = ImplClassField(_EnumImpl)
    assert field.choices_enum is enum
    assert enum.__name__ == "EnumTestImpl"
    assert enum.choices == [("base", "base")]
    with override_settings(ANGEE_ENUM_TEST_CLASSES={"base": "tests.test_impl._EnumRefinedImpl"}):
        assert impl_choices_enum(_EnumImpl) is enum
    with override_settings(
        ANGEE_ENUM_TEST_CLASSES={
            "refined": "tests.test_impl._EnumRefinedImpl",
            "base": "tests.test_impl._EnumImpl",
        }
    ):
        expanded = impl_choices_enum(_EnumImpl)
        assert expanded is not enum
        assert expanded.choices == [("base", "base"), ("refined", "refined")]
    assert impl_choices_enum(_EnumImpl) is enum


@override_settings(ANGEE_ENUM_TEST_CLASSES={"base": "tests.test_impl._EnumImpl"})
def test_an_implementation_key_pickles_as_its_own_member() -> None:
    """A runtime-built enum has no module name to pickle by, yet a key and a query filtering on it pickle."""

    member = ImplClassField(_EnumImpl).to_python("base")
    assert pickle.loads(pickle.dumps(member)) is member
    query = VcsBridge.objects.filter(backend_class="stub").query
    assert str(pickle.loads(pickle.dumps(query))) == str(query)


def test_an_implementation_key_unpickles_as_the_member_it_was_after_its_mapping_changed() -> None:
    """Unpickling reads no settings: a key keeps its member, and the field over the new mapping accepts it."""

    with override_settings(ANGEE_ENUM_TEST_CLASSES={"base": "tests.test_impl._EnumImpl"}):
        member = ImplClassField(_EnumImpl).to_python("base")
    with override_settings(
        ANGEE_ENUM_TEST_CLASSES={"base": "tests.test_impl._EnumImpl", "refined": "tests.test_impl._EnumRefinedImpl"}
    ):
        field = ImplClassField(_EnumImpl)
        current = field.to_python("base")
        unpickled = pickle.loads(pickle.dumps(member))
        assert current is not member
        assert unpickled is member
        assert field.to_python(unpickled) is current


def test_a_row_holding_an_implementation_key_pickles() -> None:
    """A loaded row holds its key as the member, and the row pickles with it."""

    field = VcsBridge._meta.get_field("backend_class")
    member = field.from_db_value("stub", None, None)
    assert pickle.loads(pickle.dumps(VcsBridge(backend_class=member))).backend_class is member


def test_an_implementation_key_unpickles_in_a_process_without_settings() -> None:
    """Another interpreter, with no settings and no enum built yet, restores the key."""

    member = VcsBridge._meta.get_field("backend_class").from_db_value("stub", None, None)
    probe = (
        "import pickle, sys\n"
        "from django.conf import settings\n"
        "key = pickle.loads(sys.stdin.buffer.read())\n"
        "assert not settings.configured\n"
        "print(type(key).__name__, key.value, key.label)\n"
    )
    env = {name: value for name, value in os.environ.items() if name != "DJANGO_SETTINGS_MODULE"}
    result = subprocess.run(
        [sys.executable, "-c", probe], input=pickle.dumps(member), capture_output=True, env=env, check=True
    )
    assert result.stdout.decode().split() == [type(member).__name__, "stub", "stub"]


_IMPL_SELECTORS = sorted(
    f"{model._meta.label}.{field.name}"
    for model in apps.get_models()
    for field in model._meta.concrete_fields
    if isinstance(field, ImplClassField)
)


@pytest.mark.parametrize("selector", _IMPL_SELECTORS)
def test_a_query_on_every_key_of_an_installed_selector_pickles(selector: str) -> None:
    """A query on all of an installed selector's keys pickles back to the same SQL.

    It compares SQL only, so it proves every selector's keys reduce at all; the
    tests above pin what a key unpickles as.
    """

    label, name = selector.rsplit(".", 1)
    model = apps.get_model(label)
    keys = [member.value for member in model._meta.get_field(name).choices_enum]
    query = model._base_manager.filter(**{f"{name}__in": keys}).query
    assert str(pickle.loads(pickle.dumps(query))) == str(query)


@override_settings(ANGEE_STORAGE_BACKEND_CLASSES={"local": "angee.storage.backends.LocalBackend"})
def test_native_impl_field_uses_its_registry_key() -> None:
    """Native storage classes need no ImplBase contract or duplicate key."""

    field = ImplClassField(StorageBackend)

    assert resolve_all_impl_classes(StorageBackend) == (LocalBackend,)
    assert field.check() == []
    assert (
        impl_choices(StorageBackend)
        == [ImplChoice(key="local", label="local", icon="", category="", defaults={}, config_schema=None)]
    )


@override_settings(ANGEE_EMPTY_IMPLS={})
def test_historical_impl_field_reconstructs_without_removed_registry() -> None:
    """Serialized migration fields retain their stored default after their registry is removed."""

    with pytest.raises(ImproperlyConfigured, match="registry .* is empty"):
        ImplClassField(_EmptyImpl, default="none")

    historical = ImplClassField(registry_setting="ANGEE_EMPTY_IMPLS", default="none")
    _, _, args, kwargs = historical.deconstruct()
    reconstructed = ImplClassField(*args, **kwargs)

    assert reconstructed.base_class is None
    assert reconstructed.get_default() == "none"
    assert reconstructed.deconstruct()[3]["registry_setting"] == "ANGEE_EMPTY_IMPLS"


@override_settings(ANGEE_RENAMED_IMPLS={})
def test_historical_impl_field_loads_when_its_registry_was_renamed() -> None:
    """A migration state whose registry setting no longer exists still loads as a column."""

    historical = ImplClassField(editable=False, max_length=100, registry_setting="ANGEE_RENAMED_IMPLS")
    _, _, args, kwargs = historical.deconstruct()
    reconstructed = ImplClassField(*args, **kwargs)

    assert reconstructed.base_class is None
    assert "choices" not in reconstructed.deconstruct()[3]
    assert reconstructed.deconstruct()[3]["registry_setting"] == "ANGEE_RENAMED_IMPLS"



@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TEST_IMPLS={"typed": "tests.test_impl._TypedConfigImpl"})
@isolate_apps()
def test_base_validation_refreshes_and_normalizes_deferred_config() -> None:
    """Inherited validation refreshes deferred config before normalizing a write."""

    class DeferredConfigRecord(ImplDefaultsMixin):
        adapter = ImplClassField(_BaseImpl, create_only=True)
        config = models.JSONField(default=dict)

        class Meta:
            app_label = "tests"

    with model_tables((DeferredConfigRecord,)):
        record = DeferredConfigRecord.objects.create(adapter="typed", config={"retries": 1})
        DeferredConfigRecord.objects.filter(pk=record.pk).update(config={"retries": "3"})
        deferred = DeferredConfigRecord.objects.only("pk").get(pk=record.pk)
        # Pin the deferred setup whose explicit config write must still validate.
        assert deferred.get_deferred_fields() == {"adapter", "config"}

        deferred.save(update_fields={"config"})
        stored = DeferredConfigRecord.objects.get(pk=record.pk)

        assert stored.config == {"endpoint": "https://example.test", "retries": 3}


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TEST_IMPLS={"typed": "tests.test_impl._TypedConfigImpl"})
@isolate_apps()
def test_impl_refresh_accepts_native_queryset_and_iterable_fields() -> None:
    """Native hydration leaves the create-only key ready for a later config write."""

    class RefreshRecord(ImplDefaultsMixin):
        adapter = ImplClassField(_BaseImpl, create_only=True)
        config = models.JSONField(default=dict)

        class Meta:
            app_label = "tests"

    with model_tables((RefreshRecord,)):
        record = RefreshRecord.objects.create(adapter="typed", config={"retries": 1})
        deferred = RefreshRecord.objects.only("pk", "config").get(pk=record.pk)
        deferred.refresh_from_db(None, iter(("adapter",)), RefreshRecord.objects.filter(pk=record.pk))
        deferred.config = {"retries": "4"}
        deferred.save(update_fields={"config"})
        stored = RefreshRecord.objects.get(pk=record.pk)
        assert stored.adapter == "typed"
        assert stored.config == {"endpoint": "https://example.test", "retries": 4}


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TEST_IMPLS={"typed": "tests.test_impl._TypedConfigImpl"})
@isolate_apps()
def test_impl_save_leaves_untouched_config_and_selector_deferred(django_assert_num_queries) -> None:
    """An unrelated save neither reads nor rewrites the deferred config and selector."""

    class DeferredConfigRecord(ImplDefaultsMixin):
        adapter = ImplClassField(_BaseImpl, create_only=True)
        config = models.JSONField(default=dict)
        label = models.CharField(max_length=50)

        class Meta:
            app_label = "tests"

    with model_tables((DeferredConfigRecord,)):
        record = DeferredConfigRecord.objects.create(adapter="typed", config={"retries": 1}, label="before")
        deferred = DeferredConfigRecord.objects.only("label").get(pk=record.pk)
        DeferredConfigRecord.objects.filter(pk=record.pk).update(config={"retries": 7})
        deferred.label = "after"

        with django_assert_num_queries(1):
            deferred.save()

        assert "config" not in deferred.__dict__
        assert "adapter" not in deferred.__dict__
        stored = DeferredConfigRecord.objects.get(pk=record.pk)
        assert stored.label == "after"
        assert stored.config == {"retries": 7}
        assert stored.adapter == "typed"


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TEST_IMPLS={"typed": "tests.test_impl._TypedConfigImpl"})
@isolate_apps()
def test_a_model_naming_its_configuration_column_stores_it_validated_and_refuses_under_that_name() -> None:
    """``impl_config_field`` names the column: every save stores the validated value there, system work included."""

    class NamedConfigRecord(ImplDefaultsMixin):
        impl_config_field = "settings"
        adapter = ImplClassField(_BaseImpl)
        settings = models.JSONField(default=dict)
        label = models.CharField(max_length=50, default="")

        class Meta:
            app_label = "tests"

    assert NamedConfigRecord.check() == []
    with model_tables((NamedConfigRecord,)):
        record = NamedConfigRecord.objects.create(adapter="typed", settings={"retries": "2"})
        stored = NamedConfigRecord.objects.get(pk=record.pk)
        assert stored.settings == {"endpoint": "https://example.test", "retries": 2}

        record.settings = {"retries": "many"}
        with pytest.raises(ValidationError) as refused:
            record.save()
        assert list(refused.value.message_dict) == ["settings.retries"]
        assert NamedConfigRecord.objects.get(pk=record.pk).settings["retries"] == 2

        untouched = NamedConfigRecord.objects.only("label").get(pk=record.pk)
        untouched.label = "renamed"
        untouched.save(update_fields=["label"])
        assert "settings" not in untouched.__dict__

        assert record.apply_config_patch({"retries": 5}) == {"settings"}
        with pytest.raises(ValidationError) as patched:
            record.apply_config_patch(["retries"])
        assert list(patched.value.message_dict) == ["settings"]

        seeded = NamedConfigRecord(adapter="typed")
        assert seeded.materialize_impl_defaults(_TypedConfigImpl) == {"settings"}
        assert seeded.settings == {"endpoint": "https://example.test"}


@override_settings(ANGEE_TEST_IMPLS={"typed": "tests.test_impl._TypedConfigImpl"})
@isolate_apps()
def test_a_model_without_the_column_it_names_fails_the_typed_config_check() -> None:
    class MissingConfigRecord(ImplDefaultsMixin):
        impl_config_field = "settings"
        adapter = ImplClassField(_BaseImpl)
        config = models.JSONField(default=dict)

        class Meta:
            app_label = "tests"

    (error,) = MissingConfigRecord.check()

    assert (error.id, error.msg) == ("angee.E046", "MissingConfigRecord has no field named 'settings'")


def test_the_configuration_column_stays_config_for_every_composer_that_names_none() -> None:
    from angee.agents.testing.models import InferenceProvider
    from angee.integrate.testing.integration import Integration

    assert ImplDefaultsMixin.impl_config_field == "config"
    for model in (Integration, OAuthClient, InferenceProvider, VcsBridge):
        assert model.impl_config_field == "config"
        assert [error.id for error in model.check() if error.id in {"angee.E046", "angee.E047"}] == []
    assert VcsBridge._config_field().name == "config"


def test_effective_defaults_merges_along_mro() -> None:
    """A refinement inherits its base's defaults and overrides only what it restates."""

    assert _RefinedImpl.effective_defaults() == {
        "authorize_endpoint": "https://base/authorize",  # inherited
        "token_endpoint": "https://refined/token",  # overridden
        "userinfo_endpoint": "https://refined/userinfo",  # added
    }
    # The base is unaffected by the refinement's overrides.
    assert _BaseImpl.effective_defaults() == {
        "authorize_endpoint": "https://base/authorize",
        "token_endpoint": "https://base/token",
    }


def test_effective_defaults_deep_merges_dict_defaults() -> None:
    """A dict-valued default merges one level deep along the MRO, not replaced wholesale."""

    assert _ConfigRefined.effective_defaults()["authorize_params"] == {
        "host": "base",  # inherited from the base dict
        "port": 2,  # overridden
        "tls": True,  # added
    }


def test_choice_metadata_falls_back_to_titlecased_key() -> None:
    """``choice`` projects pickable metadata; label falls back to the key, category inherits."""

    assert _RefinedImpl.choice() == ImplChoice(
        key="refined",
        label="Refined",
        icon="",
        category="demo",
        defaults=_RefinedImpl.effective_defaults(),
        config_schema=None,
    )


def test_config_patch_preserves_siblings_and_reports_changed_model_fields() -> None:
    """Patches replace supplied keys, remove nulls, and leave the input object alone."""

    original = {"endpoint": "https://example.test", "obsolete": True, "options": {"old": 1}}
    bridge = VcsBridge(config=original)

    assert bridge.apply_config_patch({"obsolete": None, "options": {"new": 2}}) == {"config"}
    assert bridge.config == {"endpoint": "https://example.test", "options": {"new": 2}}
    assert original == {"endpoint": "https://example.test", "obsolete": True, "options": {"old": 1}}
    assert bridge.apply_config_patch({"obsolete": None, "options": {"new": 2}}) == set()
    assert bridge.apply_config_patch({}) == set()


def test_typed_config_projects_supported_scalars_and_validates_paths() -> None:
    """The native declaration owns defaults, form metadata, and validation paths."""

    assert _TypedConfigImpl.effective_defaults()["config"] == {"endpoint": "https://example.test"}
    assert _TypedConfigImpl.config_form_spec() == {
        "type": "object",
        "properties": {
            "endpoint": {
                "type": "string",
                "label": "Endpoint",
                "description": "Service endpoint.",
                "defaultValue": "https://example.test",
                "omittable": True,
            },
            "retries": {"type": "integer", "label": "Retries", "presenceRequired": True},
        },
        "required": ["retries"],
    }

    with pytest.raises(ValidationError, match="config.retries"):
        _TypedConfigImpl.normalize_config({"endpoint": "https://example.test"})


def test_config_parsing_returns_model_and_normalizes_once_with_wire_aliases() -> None:
    """Parsing and JSON normalization share one validation pass and native aliases."""

    seen = []

    class Config(BaseModel):
        amount: int = Field(alias="wireAmount")

        @field_validator("amount")
        @classmethod
        def increment(cls, value: int) -> int:
            seen.append(value)
            return value + 1

    class ConfiguredImpl(ImplBase):
        config_model = Config

    raw = {"wireAmount": "2"}
    parsed = ConfiguredImpl.parse_config(raw)
    assert isinstance(parsed, Config)
    assert parsed.amount == 3
    assert seen == [2]
    seen.clear()
    assert ConfiguredImpl.normalize_config(raw) == {"wireAmount": 3}
    assert seen == [2]
    assert raw == {"wireAmount": "2"}


def test_config_parsing_translates_nested_alias_and_index_paths() -> None:
    """Typed errors retain native wire aliases and collection indexes for Django callers."""

    class Child(BaseModel):
        value: int = Field(alias="wireValue")

    class Config(BaseModel):
        items: list[Child]

    class ConfiguredImpl(ImplBase):
        config_model = Config

    with pytest.raises(ValidationError) as error:
        ConfiguredImpl.parse_config({"items": [{"wireValue": "invalid"}, {}]})
    assert set(error.value.message_dict) == {"config.items.0.wireValue", "config.items.1.wireValue"}


@pytest.mark.parametrize("impl", [LocalVCSBackend, GitHubBackend])
def test_production_backend_configs_share_typed_parsing(impl: type[ImplBase]) -> None:
    """Every declared backend config keeps its model defaults and extra-key policy."""

    parsed = impl.parse_config({})
    assert isinstance(parsed, impl.config_model)
    assert impl.normalize_config({}) == parsed.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError) as error:
        impl.parse_config({"unknown_option": True})
    assert set(error.value.message_dict) == {"config.unknown_option"}


def test_no_model_config_parser_rejects_values_without_changing_untyped_rows() -> None:
    """Explicit typed parsing rejects config without a model; legacy row config stays open."""

    for parse in (_BaseImpl.parse_config, _BaseImpl.normalize_config):
        with pytest.raises(ValidationError, match="does not accept configuration"):
            parse({"undeclared": True})
    assert _BaseImpl.parse_config({}) is None
    assert _BaseImpl.normalize_config({}) == {}

    raw = {"arbitrary": ["retained"]}
    bridge = VcsBridge(backend_class="stub", config=raw)
    impl = VcsBridge._meta.get_field("backend_class").resolve_class("stub")
    assert impl.config_model is None
    bridge.validate_impl_configs()
    assert bridge.config is raw


def test_typed_config_defaults_do_not_depend_on_form_projection() -> None:
    """Backend defaults remain available for declarations the bounded UI cannot render."""

    class BackendConfig(BaseModel):
        required: str
        headers: dict[str, str] = {"Accept": "application/json"}
        blank: str = ""
        optional: str | None = None
        enabled: bool = False
        retries: int = 0

    class BackendImpl(ImplBase):
        config_model = BackendConfig

    assert BackendImpl.config_defaults() == {
        "headers": {"Accept": "application/json"},
        "enabled": False,
        "retries": 0,
    }
    assert BackendImpl.effective_defaults()["config"] == BackendImpl.config_defaults()
    with pytest.raises(ImproperlyConfigured, match=r"config\.headers.*additionalProperties"):
        BackendImpl.config_form_spec()


def test_typed_config_defaults_preserve_json_aliases_and_mutable_isolation() -> None:
    """Static defaults retain aliases, native JSON encoding and mutable isolation."""

    class Mode(str, Enum):
        SAFE = "safe"

    class NestedConfig(BaseModel):
        tags: list[str] = Field(default=["declared"], alias="wireTags")

    class BackendConfig(BaseModel):
        model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

        nested: NestedConfig = Field(default=NestedConfig(), alias="wireNested")
        scheduled: date = date(2026, 9, 20)
        mode: Mode = Mode.SAFE
        encoded: bytes = b"declared"

    class BackendImpl(ImplBase):
        config_model = BackendConfig

    expected = {
        "wireNested": {"wireTags": ["declared"]},
        "scheduled": "2026-09-20",
        "mode": "safe",
        "encoded": "ZGVjbGFyZWQ=",
    }
    first = BackendImpl.config_defaults()
    second = BackendImpl.config_defaults()
    assert first == second == BackendImpl.normalize_config(first) == expected
    first["wireNested"]["wireTags"].append("changed")
    assert second == BackendImpl.config_defaults() == expected


def test_typed_config_defaults_are_inputs_before_output_serializers_and_exclusions() -> None:
    """Suggestions preserve input defaults; normalization owns the model's output rules."""

    class BackendConfig(BaseModel):
        value: Annotated[int, PlainSerializer(lambda value: f"{value:02d}", return_type=str)] = 7
        internal_token: str = Field(default="seed", exclude=True)

    class BackendImpl(ImplBase):
        config_model = BackendConfig

    suggestions = BackendImpl.config_defaults()
    assert suggestions == {"value": 7, "internal_token": "seed"}
    assert BackendImpl.normalize_config(suggestions) == {"value": "07"}
    spec = BackendImpl.config_form_spec()
    assert spec is not None
    assert spec["properties"]["value"]["defaultValue"] == 7


def test_typed_config_defaults_compose_native_json_schema_declarations() -> None:
    """Native schema customization supplies the same suggestion to backend and form."""

    class BackendConfig(BaseModel):
        value: int = Field(default=7, json_schema_extra={"default": 9})

    class BackendImpl(ImplBase):
        config_model = BackendConfig

    assert BackendImpl.config_defaults() == {"value": 9}
    spec = BackendImpl.config_form_spec()
    assert spec is not None
    assert spec["properties"]["value"]["defaultValue"] == 9


def test_typed_config_projects_native_input_constraints() -> None:
    """Native string and numeric constraints survive the form projection."""

    class ConstrainedConfig(BaseModel):
        token: str = Field(min_length=8)
        attempts: int = Field(ge=1, le=5)
        names: list[str] = Field(min_length=1, max_length=3)

    class ConstrainedImpl(ImplBase):
        config_model = ConstrainedConfig

    spec = ConstrainedImpl.config_form_spec()
    assert spec is not None
    assert spec["properties"]["token"]["minLength"] == 8
    assert spec["properties"]["attempts"]["minimum"] == 1
    assert spec["properties"]["attempts"]["maximum"] == 5
    assert spec["properties"]["names"]["minItems"] == 1
    assert spec["properties"]["names"]["maxItems"] == 3


@pytest.mark.parametrize(("annotation", "declared"), [
    (date, {"type": "string", "format": "date", "widget": "date"}),
    (datetime, {"type": "string", "format": "date-time", "widget": "datetime"}),
], ids=["date", "date-time"])
@pytest.mark.parametrize("shape", ["plain", "nullable", "list", "nested"])
def test_typed_config_projects_native_date_widgets(annotation: type, declared: dict[str, str], shape: str) -> None:
    """A Pydantic date or date and time keeps its declared format beside the date control it selects, at every depth."""

    required = {"label": "Value", "presenceRequired": True}
    member, expected = {
        "plain": ((annotation, ...), {**declared, **required}),
        "nullable": (
            (annotation | None, None),
            {**declared, "nullable": True, "label": "Value", "defaultValue": None, "omittable": True},
        ),
        "list": ((list[annotation], ...), {"type": "array", "widget": "list", "items": declared, **required}),
        "nested": ((create_model("Inner", value=(annotation, ...)), ...), {
            "type": "object", "widget": "object", "properties": {"value": {**declared, **required}},
            "required": ["value"], **required,
        }),
    }[shape]

    class DateImpl(ImplBase):
        config_model = create_model("DateConfig", value=member)

    spec = DateImpl.config_form_spec()
    assert spec is not None
    assert spec["properties"]["value"] == expected


def test_typed_config_keeps_a_date_format_under_an_authored_widget() -> None:
    """An authored widget replaces the control and keeps the format; an authored JSON widget carries none."""

    class AuthoredConfig(BaseModel):
        day: date = Field(json_schema_extra={"widget": "text"})
        moment: datetime = Field(json_schema_extra={"widget": "date"})
        raw: date = Field(json_schema_extra={"widget": "json"})
        started: datetime = datetime(2024, 1, 31, 12, 0)

    class AuthoredImpl(ImplBase):
        config_model = AuthoredConfig

    spec = AuthoredImpl.config_form_spec()
    assert spec is not None
    required = {"presenceRequired": True}
    assert spec["properties"]["day"] == {
        "type": "string", "format": "date", "widget": "text", "label": "Day", **required,
    }
    assert spec["properties"]["moment"] == {
        "type": "string", "format": "date-time", "widget": "date", "label": "Moment", **required,
    }
    # An authored JSON widget renders the member as raw JSON: no declared type travels with it.
    assert spec["properties"]["raw"] == {"type": "any", "widget": "json", "label": "Raw", **required}
    # A default the typed model accepts stands beside the format even where the format's own grammar
    # (RFC 3339 wants an offset) refuses it: nothing asserts the format on the projected spec.
    assert spec["properties"]["started"] == {
        "type": "string", "format": "date-time", "widget": "datetime", "label": "Started",
        "defaultValue": "2024-01-31T12:00:00", "omittable": True,
    }


def test_typed_config_projects_integer_exclusive_bounds_exactly() -> None:
    class BoundedIntegerConfig(BaseModel):
        count: int = Field(json_schema_extra={
            "minimum": 2,
            "exclusiveMinimum": 1.5,
            "maximum": 8,
            "exclusiveMaximum": 8.5,
        })

    class BoundedIntegerImpl(ImplBase):
        config_model = BoundedIntegerConfig

    spec = BoundedIntegerImpl.config_form_spec()
    assert spec is not None
    assert spec["properties"]["count"]["minimum"] == 2
    assert spec["properties"]["count"]["maximum"] == 8


def test_typed_config_rejects_exclusive_number_bounds_and_free_form_structured_fields() -> None:
    class ExclusiveNumberConfig(BaseModel):
        ratio: float = Field(gt=1.5)

    class ExclusiveNumberImpl(ImplBase):
        config_model = ExclusiveNumberConfig

    with pytest.raises(ImproperlyConfigured, match="exclusive numeric bound"):
        ExclusiveNumberImpl.config_form_spec()

    class FreeFormConfig(BaseModel):
        values: dict[str, str]

    class FreeFormImpl(ImplBase):
        config_model = FreeFormConfig

    with pytest.raises(ImproperlyConfigured, match="mapping/additionalProperties"):
        FreeFormImpl.config_form_spec()

    class EmptyExtensibleObject(BaseModel):
        model_config = ConfigDict(extra="allow")

    class NestedExtensibleConfig(BaseModel):
        values: EmptyExtensibleObject

    class NestedExtensibleImpl(ImplBase):
        config_model = NestedExtensibleConfig

    with pytest.raises(ImproperlyConfigured, match="free-form mapping"):
        NestedExtensibleImpl.config_form_spec()

    class DeclaredExtensibleObject(BaseModel):
        model_config = ConfigDict(extra="allow")

        label: str

    class NestedDeclaredExtensibleConfig(BaseModel):
        values: DeclaredExtensibleObject

    class NestedDeclaredExtensibleImpl(ImplBase):
        config_model = NestedDeclaredExtensibleConfig

    with pytest.raises(ImproperlyConfigured, match="free-form mapping"):
        NestedDeclaredExtensibleImpl.config_form_spec()


def test_typed_config_projects_explicit_dynamic_json_and_datetime_widgets() -> None:
    """Dynamic JSON is opt-in while Pydantic datetimes use the native picker."""

    class PresentationConfig(BaseModel):
        options: dict[str, object] = Field(json_schema_extra={"widget": "json"})
        scheduled_at: datetime

    class PresentationImpl(ImplBase):
        config_model = PresentationConfig

    spec = PresentationImpl.config_form_spec()
    assert spec is not None
    assert spec["properties"]["options"] == {
        "type": "object",
        "widget": "json",
        "label": "Options",
        "presenceRequired": True,
    }
    assert spec["properties"]["scheduled_at"] == {
        "type": "string",
        "format": "date-time",
        "widget": "datetime",
        "label": "Scheduled At",
        "presenceRequired": True,
    }


def test_typed_config_validates_relation_form_metadata_at_projection() -> None:
    """Relation extensions fail at their backend owner instead of in the browser."""

    relation = {
        "resource": "demo.Target",
        "permission": "read",
        "labelField": "name",
        "filters": [{"operator": "and", "value": [
            {"field": "status", "operator": "eq", "value": "active"},
        ]}],
        "create": {"resource": "demo.Target", "defaultValues": {"status": "active"}},
    }

    class RelatedConfig(BaseModel):
        target_id: str = Field(json_schema_extra={"relation": relation})

    spec = config_form_spec(RelatedConfig.model_json_schema(by_alias=True), owner="RelatedConfig")
    assert spec["properties"]["target_id"]["relation"] == relation

    class InvalidFilterConfig(BaseModel):
        target_id: str = Field(json_schema_extra={"relation": {
            "resource": "demo.Target",
            "filters": [{"field": "status", "operator": "approximately", "value": "active"}],
        }})

    with pytest.raises(ImproperlyConfigured, match="invalid relation"):
        config_form_spec(InvalidFilterConfig.model_json_schema(by_alias=True), owner="InvalidFilterConfig")

    class InvalidKindConfig(BaseModel):
        target_ids: list[str] = Field(json_schema_extra={"relation": {"resource": "demo.Target"}})

    with pytest.raises(ImproperlyConfigured, match="relation on a non-string field"):
        config_form_spec(InvalidKindConfig.model_json_schema(by_alias=True), owner="InvalidKindConfig")

    class InvalidWidgetConfig(BaseModel):
        target_id: str = Field(json_schema_extra={
            "widget": "text", "relation": {"resource": "demo.Target"},
        })

    with pytest.raises(ImproperlyConfigured, match="relation with a non-relation widget"):
        config_form_spec(InvalidWidgetConfig.model_json_schema(by_alias=True), owner="InvalidWidgetConfig")


def test_typed_config_projects_nested_arrays_nullable_and_enum_contracts() -> None:
    """Pydantic's recursive schema remains the SSOT for the supported FormSpec subset."""

    class Mode(str, Enum):
        FAST = "fast"
        SAFE = "safe"

    class Credentials(BaseModel):
        username: str
        note: str | None = None

    class RecursiveConfig(BaseModel):
        credentials: Credentials
        mirrors: list[Credentials]
        tags: list[str]
        nullable_required: int | None
        nullable_default: int | None = None
        mode: Mode = Mode.SAFE
        strategy: Literal["append", "replace"] = "append"
        fixed: Literal["only"] = "only"

    class RecursiveImpl(ImplBase):
        config_model = RecursiveConfig

    spec = RecursiveImpl.config_form_spec()
    assert spec is not None
    assert spec["required"] == ["credentials", "mirrors", "tags", "nullable_required"]
    assert spec["properties"]["credentials"] == {
        "type": "object",
        "widget": "object",
        "properties": {
            "username": {"type": "string", "label": "Username", "presenceRequired": True},
            "note": {
                "type": "string",
                "nullable": True,
                "label": "Note",
                "defaultValue": None,
                "omittable": True,
            },
        },
        "required": ["username"],
        "label": "Credentials",
        "presenceRequired": True,
    }
    assert spec["properties"]["mirrors"]["items"]["type"] == "object"
    assert spec["properties"]["mirrors"]["widget"] == "list"
    assert spec["properties"]["mirrors"]["items"]["widget"] == "object"
    assert spec["properties"]["tags"]["items"] == {"type": "string"}
    assert spec["properties"]["tags"]["widget"] == "list"
    assert spec["properties"]["nullable_required"] == {
        "type": "integer",
        "nullable": True,
        "label": "Nullable Required",
        "presenceRequired": True,
    }
    assert spec["properties"]["nullable_default"] == {
        "type": "integer",
        "nullable": True,
        "label": "Nullable Default",
        "defaultValue": None,
        "omittable": True,
    }
    assert "nullable_required" in spec["required"]
    assert "nullable_default" not in spec["required"]
    assert "defaultValue" not in spec["properties"]["nullable_required"]
    assert spec["properties"]["nullable_default"]["defaultValue"] is None
    assert spec["properties"]["mode"] == {
        "type": "string",
        "enum": ["fast", "safe"],
        "label": "Mode",
        "defaultValue": "safe",
        "omittable": True,
    }
    assert spec["properties"]["strategy"]["enum"] == ["append", "replace"]
    assert spec["properties"]["fixed"] == {
        "type": "string",
        "const": "only",
        "enum": ["only"],
        "label": "Fixed",
        "defaultValue": "only",
        "omittable": True,
    }


def test_typed_config_omits_default_factory_without_invoking_it() -> None:
    """Build-time metadata never executes a dynamic Pydantic default factory."""

    calls = 0

    def make_generated() -> list[str]:
        nonlocal calls
        calls += 1
        return ["runtime"]

    class FactoryConfig(BaseModel):
        generated: list[str] = Field(default_factory=make_generated)
        static: list[str] = ["declared"]

    class FactoryImpl(ImplBase):
        config_model = FactoryConfig

    spec = FactoryImpl.config_form_spec()
    assert spec is not None
    assert calls == 0
    assert "defaultValue" not in spec["properties"]["generated"]
    assert spec["properties"]["static"]["defaultValue"] == ["declared"]
    assert FactoryImpl.config_defaults() == {"static": ["declared"]}
    assert FactoryImpl.choice().defaults == {"config": {"static": ["declared"]}}
    assert calls == 0
    assert FactoryImpl.normalize_config({}) == {"generated": ["runtime"], "static": ["declared"]}
    assert calls == 1


@pytest.mark.parametrize(
    ("annotation", "field_path", "detail"),
    [
        (str | int, "config.payload", "union"),
        (list[str | int], "config.payload[]", "union"),
        (dict[str, str], "config.payload", "mapping/additionalProperties"),
        (tuple[str, int], "config.payload", "keywords prefixItems"),
        (time, "config.payload", "format 'time'"),
        (timedelta, "config.payload", "format 'duration'"),
        (UUID, "config.payload", "format 'uuid'"),
        (AnyUrl, "config.payload", "format 'uri'"),
    ],
)
def test_typed_config_rejects_unsupported_shapes_with_exact_path(
    annotation: object, field_path: str, detail: str,
) -> None:
    """A shape FormSpec cannot preserve fails at the declaring field path."""

    UnsupportedConfig = type(
        "UnsupportedConfig",
        (BaseModel,),
        {"__annotations__": {"payload": annotation}},
    )

    class UnsupportedImpl(ImplBase):
        config_model = UnsupportedConfig

    with pytest.raises(ImproperlyConfigured) as caught:
        UnsupportedImpl.config_form_spec()
    assert str(caught.value) == f"UnsupportedImpl.config_model field {field_path!r} uses unsupported schema: {detail}."


def test_config_form_spec_resolves_escaped_local_references(monkeypatch: pytest.MonkeyPatch) -> None:
    class ReferencedConfig(BaseModel):
        value: str

    schema = ReferencedConfig.model_json_schema()
    schema["$defs"] = {"Text/~ value": {"type": "string", "minLength": 2}}
    schema["properties"]["value"] = {"$ref": "#/$defs/Text~1~0%20value", "title": "Value"}
    monkeypatch.setattr(ReferencedConfig, "model_json_schema", lambda **kwargs: schema)
    spec = config_form_spec(ReferencedConfig.model_json_schema(by_alias=True), owner="ReferencedConfig")

    assert spec["properties"]["value"] == {
        "type": "string", "minLength": 2, "label": "Value", "presenceRequired": True,
    }


@pytest.mark.parametrize("reference", ["#/$defs/Missing", "https://example.test/schema"])
def test_config_form_spec_rejects_unresolved_or_remote_references(
    reference: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReferencedConfig(BaseModel):
        value: str

    schema = ReferencedConfig.model_json_schema()
    schema["properties"]["value"] = {"$ref": reference}
    monkeypatch.setattr(ReferencedConfig, "model_json_schema", lambda **kwargs: schema)
    with pytest.raises(ImproperlyConfigured, match=r"ReferencedConfig.*config\.value.*reference"):
        config_form_spec(ReferencedConfig.model_json_schema(by_alias=True), owner="ReferencedConfig")


def test_config_form_spec_rejects_pointers_into_scoped_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    class ReferencedConfig(BaseModel):
        value: str

    schema = ReferencedConfig.model_json_schema()
    schema["$defs"] = {"Scoped": {"$id": "child", "$defs": {"Value": {"type": "string"}}}}
    schema["properties"]["value"] = {"$ref": "#/$defs/Scoped/$defs/Value"}
    monkeypatch.setattr(ReferencedConfig, "model_json_schema", lambda **kwargs: schema)
    with pytest.raises(ImproperlyConfigured, match=r"ReferencedConfig.*config\.value.*reference"):
        config_form_spec(ReferencedConfig.model_json_schema(by_alias=True), owner="ReferencedConfig")


def test_typed_config_rejects_recursive_models_and_preserves_string_aliases() -> None:
    """References stay finite and one string alias remains the config wire identity."""

    class RecursiveNode(BaseModel):
        child: "RecursiveNode | None" = None

    class RecursiveImpl(ImplBase):
        config_model = RecursiveNode

    with pytest.raises(ImproperlyConfigured, match=r"RecursiveImpl.*config\.child.*recursive reference"):
        RecursiveImpl.config_form_spec()

    class AliasedChild(BaseModel):
        value: str = Field(alias="wireValue")

    class AliasedConfig(BaseModel):
        child: AliasedChild

    class AliasedImpl(ImplBase):
        config_model = AliasedConfig

    spec = AliasedImpl.config_form_spec()
    assert spec is not None
    assert "wireValue" in spec["properties"]["child"]["properties"]
    assert AliasedImpl.normalize_config({"child": {"wireValue": "kept"}}) == {
        "child": {"wireValue": "kept"},
    }

    class AmbiguousConfig(BaseModel):
        value: str = Field(validation_alias=AliasChoices("value", "wireValue"))

    class AmbiguousImpl(ImplBase):
        config_model = AmbiguousConfig

    with pytest.raises(ImproperlyConfigured, match=r"AmbiguousImpl.*config\.value.*one string alias"):
        AmbiguousImpl.config_form_spec()
    with pytest.raises(ImproperlyConfigured, match=r"AmbiguousImpl.*config\.value.*one string alias"):
        AmbiguousImpl.config_defaults()

    class CollidingConfig(BaseModel):
        value: str = Field(alias="wireValue")
        wire_value: str = Field(alias="wireValue")

    class CollidingImpl(ImplBase):
        config_model = CollidingConfig

    with pytest.raises(ImproperlyConfigured, match=r"CollidingImpl.*colliding wire names.*wireValue"):
        CollidingImpl.config_form_spec()
    with pytest.raises(ImproperlyConfigured, match=r"CollidingImpl.*colliding wire names.*wireValue"):
        CollidingImpl.config_defaults()


def test_materialize_seeds_only_unprovided_fields() -> None:
    """Materialise fills fields the caller did not supply; a supplied field is kept."""

    client = OAuthClient(authorize_endpoint="https://kept/authorize")
    changed = client.materialize_impl_defaults(_RefinedImpl, provided=frozenset({"authorize_endpoint"}))
    assert client.authorize_endpoint == "https://kept/authorize"  # supplied → kept
    assert client.token_endpoint == "https://refined/token"  # unsupplied → seeded
    assert client.userinfo_endpoint == "https://refined/userinfo"  # unsupplied → seeded
    assert changed == {"token_endpoint", "userinfo_endpoint"}


def test_materialize_seeds_boolean_default_when_unprovided() -> None:
    """A boolean default lands when omitted (the create-path fix), not just blank scalars."""

    client = OAuthClient()  # login_enabled model default is False
    client.materialize_impl_defaults(_BoolImpl, provided=frozenset())
    assert client.login_enabled is True


def test_materialize_keeps_explicit_value_equal_to_default() -> None:
    """A supplied value is never overwritten, even when it equals the model default."""

    client = OAuthClient(login_enabled=False)
    changed = client.materialize_impl_defaults(_BoolImpl, provided=frozenset({"login_enabled"}))
    assert client.login_enabled is False  # caller's explicit False survives the impl's True
    assert changed == set()


def test_post_construction_ingress_can_mark_explicit_fields() -> None:
    """Structured loaders preserve values assigned after Django constructed the row."""

    client = OAuthClient()
    client.login_enabled = False
    client.mark_impl_provided_fields({"login_enabled"})

    changed = client.materialize_impl_defaults(_BoolImpl, provided=client._impl_provided_fields)

    assert client.login_enabled is False
    assert changed == set()


def test_materialize_deep_copies_mutable_defaults() -> None:
    """Each row gets its own copy of a dict default — never the shared class object."""

    first = OAuthClient()
    second = OAuthClient()
    first.materialize_impl_defaults(_ConfigBase, provided=frozenset())
    second.materialize_impl_defaults(_ConfigBase, provided=frozenset())
    assert first.authorize_params == second.authorize_params
    assert first.authorize_params is not second.authorize_params
    assert first.authorize_params is not _ConfigBase.defaults["authorize_params"]


def test_materialize_fk_default_requires_declared_slug() -> None:
    """String FK impl defaults fail fast when the related model has no slug key."""

    class NoSlug(models.Model):
        """Related model without the declared natural key."""

        name = models.CharField(max_length=32)

        class Meta:
            app_label = "tests"

    class NeedsNoSlug(ImplDefaultsMixin):
        """Model receiving an FK impl default."""

        target = models.ForeignKey(NoSlug, on_delete=models.CASCADE)

        class Meta:
            app_label = "tests"

    class _BrokenFkImpl(ImplBase):
        key = "broken"
        defaults = {"target": "missing"}

    with pytest.raises(FieldDoesNotExist):
        NeedsNoSlug().materialize_impl_defaults(_BrokenFkImpl, provided=frozenset())


def test_form_spec_json_fields_preserve_structured_siblings_and_typed_validation():
    """Unstructured config leaves compose the JSON widget without weakening parsing."""
    class OpenConfig(BaseModel):
        label: str = "Example"
        payload: dict[str, int] = Field(default_factory=dict)
        timeout: float = Field(default=10, gt=0)

    spec = config_form_spec(OpenConfig.model_json_schema(by_alias=True), owner="OpenConfig", json_fields=True)
    assert spec["properties"]["label"]["type"] == "string"
    assert spec["properties"]["payload"]["widget"] == "json"
    assert spec["properties"]["timeout"]["widget"] == "json"
    with pytest.raises(ImproperlyConfigured, match="mapping/additionalProperties"):
        config_form_spec(OpenConfig.model_json_schema(by_alias=True), owner="OpenConfig")
    class Configured(ImplBase):
        config_model = OpenConfig
    with pytest.raises(ValidationError) as refused:
        Configured.parse_config({"payload": {"value": "wrong"}, "timeout": 0})
    assert {"config.payload.value", "config.timeout"} <= set(refused.value.message_dict)


def test_json_form_projection_uses_the_same_rejection_path_for_every_nonroot_shape():
    """New unsupported constraints inherit JSON fallback without a second allow-list."""

    class Config(BaseModel):
        regular: str = "Editable"
        patterned: str = Field(default="abc", pattern="^[a-z]+$")
        unique: set[int] = Field(default_factory=set)

    class JsonImpl(ImplBase):
        config_model = Config
        config_form_spec_json_fields = True

    fields = JsonImpl.config_form_spec()["properties"]
    assert fields["regular"]["type"] == "string"
    assert fields["patterned"]["widget"] == fields["unique"]["widget"] == "json"
    with pytest.raises(ValidationError):
        JsonImpl.parse_config({"patterned": "INVALID"})
    with pytest.raises(ImproperlyConfigured, match="pattern"):
        config_form_spec(Config.model_json_schema(by_alias=True), owner="Config")


def test_json_fallback_renders_an_unsupported_format_as_an_untyped_member_without_its_format():
    class Config(BaseModel):
        at: time

    fields = config_form_spec(Config.model_json_schema(by_alias=True), owner="Config", json_fields=True)["properties"]
    assert fields["at"] == {"type": "any", "widget": "json", "label": "At", "presenceRequired": True}


def test_annotation_precheck_retains_nullable_and_referenced_relation_fields():
    class Target(str, Enum):
        first = "first"

    class Config(BaseModel):
        nullable: str | None = Field(default=None, json_schema_extra={"relation": {"resource": "demo.Target"}})
        referenced: Target = Field(json_schema_extra={"relation": {"resource": "demo.Target"}})

    fields = config_form_spec(Config.model_json_schema(by_alias=True), owner="Config", json_fields=True)["properties"]
    assert fields["nullable"]["nullable"] and fields["nullable"]["relation"]["resource"] == "demo.Target"
    assert fields["referenced"]["enum"] == ["first"] and fields["referenced"]["relation"]["resource"] == "demo.Target"


@pytest.mark.parametrize("annotation", [
    {"widget": ""}, {"widget": 1}, {"relation": {"resource": ""}},
    {"relation": {"resource": "iam.User"}, "widget": "text"},
])
def test_json_fallback_never_hides_nested_form_annotation_errors(annotation, monkeypatch):
    class Nested(BaseModel):
        value: str = Field(default="", json_schema_extra=annotation)

    class Config(BaseModel):
        nested: Nested
        mapping: dict[str, Nested]

    with pytest.raises(ImproperlyConfigured, match="form annotation"):
        config_form_spec(Config.model_json_schema(by_alias=True), owner="Config", json_fields=True)
    class BadAnnotation(_BaseImpl):
        key = "bad_annotation"
        config_model = Config
        config_form_spec_json_fields = True
    monkeypatch.setattr(importlib.import_module(__name__), "BadAnnotation", BadAnnotation, raising=False)
    with override_settings(ANGEE_TEST_IMPLS={BadAnnotation.key: "tests.test_impl.BadAnnotation"}):
        assert [error.id for error in check_impl_registry(_BaseImpl)] == ["angee.E005"]

def test_relation_annotation_errors_retain_the_native_location():
    from angee.base.schemas.forms import check_form_annotations

    relation = {"resource": "demo.Target", "create": {"resource": 0}}
    with pytest.raises(ValidationError, match="invalid relation at create.resource"):
        check_form_annotations({"type": "string", "relation": relation})

    class Config(BaseModel):
        target: str = Field(json_schema_extra={"relation": relation})

    with pytest.raises(ImproperlyConfigured, match="invalid relation at create.resource"):
        config_form_spec(Config.model_json_schema(by_alias=True), owner="Config", json_fields=True)


def test_namespaced_relation_widgets_survive_config_form_projection():
    class Config(BaseModel):
        target: str = Field(title="Target", json_schema_extra={
            "widget": "demo.target", "relation": {"resource": "demo.Target"},
        })

    schema = config_form_spec(Config.model_json_schema(by_alias=True), owner="Config")
    assert schema["properties"]["target"]["widget"] == "demo.target"
    assert schema["properties"]["target"]["relation"] == {"resource": "demo.Target"}


@override_settings(ANGEE_STORAGE_BACKEND_CLASSES={"different": "tests.test_impl._KeyedStorageBackend"})
def test_native_implementation_declared_key_must_match_the_registry() -> None:
    """Native classes may declare a key without inheriting ImplBase."""

    with pytest.raises(ImproperlyConfigured, match="with key 'declared'"):
        resolve_impl_class(StorageBackend, "different")


@pytest.mark.parametrize("name", [
    "ANGEE_MCP_ACTOR_VERIFIER", "ANGEE_PROFILING_PERMISSION", "ANGEE_GRAPHQL_WRITE_BACKEND_ADAPTER",
])
def test_optional_cross_addon_hooks_are_inactive_when_unset(settings, name):
    from django.conf import settings as django_settings

    from angee.base.hooks import check_hook, check_hooks
    from angee.mcp.verifier import _verifier
    from angee.server.profiling import profiling_permitted

    if hasattr(django_settings, name):
        delattr(settings, name)
    assert not hasattr(django_settings, name)
    assert resolve_hook(name) is None
    assert resolve_hooks(name) == ()
    assert check_hook(name) == []
    assert check_hooks((name,)) == []
    if name == "ANGEE_MCP_ACTOR_VERIFIER":
        assert _verifier() is None
    elif name == "ANGEE_PROFILING_PERMISSION":
        assert not profiling_permitted(object())


def _registry_without(field: ImplClassField, key: str) -> dict[str, dict[str, str]]:
    """The field's registry setting with ``key`` taken out, as when its providing addon leaves."""

    registry = getattr(settings, field.registry_setting)
    return {field.registry_setting: {name: path for name, path in registry.items() if name != key}}


@pytest.mark.django_db
def test_a_stored_row_saves_its_key_after_the_registry_drops_it() -> None:
    """Only a new row's key is refused: a stored row saves, fully or in part, as it always did."""

    backend_model = apps.get_model("storage", "Backend")
    with system_context(reason="test.impl.stale.create"):
        row = backend_model.objects.create(slug="stale", label="Stale", backend_class="local")
    with override_settings(**_registry_without(backend_model._meta.get_field("backend_class"), "local")):
        with system_context(reason="test.impl.stale.save"):
            row.label = "Renamed"
            row.save()
            row.save(update_fields=["label"])
    assert backend_model._base_manager.get(pk=row.pk).label == "Renamed"


@pytest.mark.django_db
@pytest.mark.parametrize("registered", (True, False), ids=("registered", "dropped"))
def test_a_config_only_save_is_validated_against_the_rows_implementation(registered: bool) -> None:
    """Writing only ``config`` still validates it, whether or not the key's implementation is still registered."""

    with system_context(reason="test.impl.config.create"):
        Vendor.objects.get_or_create(slug="github", defaults={"display_name": "GitHub"})
        row = VcsBridge.objects.create_capability(display_name="Config bridge", backend_class="github")
    stored = VcsBridge._base_manager.get(pk=row.pk).config
    dropped = {} if registered else _registry_without(VcsBridge._meta.get_field("backend_class"), "github")
    with override_settings(**dropped), system_context(reason="test.impl.config.save"):
        loaded = VcsBridge._base_manager.get(pk=row.pk)
        loaded.config = {"not_a_github_option": 1}
        with pytest.raises((ValidationError, ImproperlyConfigured)):
            loaded.save(update_fields=["config"])
    assert VcsBridge._base_manager.get(pk=row.pk).config == stored


def _retired_bridge(slug: str) -> Any:
    """A VCS bridge, a create-only selector's row whose model keeps its loaded keys, whose stored key is retired."""

    bridge = make_integration(slug, model=VcsBridge, backend_class="local")
    store_column(VcsBridge, bridge.pk, "backend_class", "retired")
    with system_context(reason="test.impl.retired.bridge"):
        return VcsBridge._base_manager.get(pk=bridge.pk)


def _a_drive() -> Any:
    """A drive on a local storage backend, which a mount needs."""

    with system_context(reason="test.impl.drive"):
        backend = apps.get_model("storage", "Backend").objects.create(
            slug="repair-drive", label="Repair", backend_class="local",
        )
        return apps.get_model("storage", "Drive")._base_manager.create(
            backend=backend, slug="repair", name="Repair", prefix="repair",
        )


def _retired_backend(slug: str) -> Any:
    """A storage backend row whose stored key no implementation is registered under any more."""

    backend_model = apps.get_model("storage", "Backend")
    with system_context(reason="test.impl.retired.create"):
        row = backend_model.objects.create(slug=slug, label=slug.title(), backend_class="local")
    store_column(backend_model, row.pk, "backend_class", "retired")
    return backend_model._base_manager.get(pk=row.pk)


@pytest.mark.django_db
def test_a_stored_key_its_choices_no_longer_name_loads_lists_and_filters_as_its_text() -> None:
    """The row loads, lists, filters, displays and pickles; resolving its implementation raises, naming all three."""

    retired = _retired_backend("retired")
    backend_model = type(retired)
    with system_context(reason="test.impl.retired.kept"):
        kept = backend_model.objects.create(slug="kept", label="Kept", backend_class="local")
    rows = backend_model._base_manager.filter(pk__in=(retired.pk, kept.pk))

    assert type(retired.backend_class) is str and retired.backend_class == "retired"
    choices = backend_model._meta.get_field("backend_class").choices_enum
    assert backend_model._base_manager.get(pk=kept.pk).backend_class is choices("local")
    assert sorted(rows.values_list("backend_class", flat=True)) == ["local", "retired"]
    assert list(rows.filter(backend_class="retired")) == [retired]
    assert list(rows.filter(backend_class__in=("retired",))) == [retired]
    assert retired.get_backend_class_display() == "retired"
    assert pickle.loads(pickle.dumps(retired)).backend_class == "retired"
    with pytest.raises(
        ImproperlyConfigured,
        match=r"^storage\.Backend\.backend_class: No impl for key 'retired' in settings\.ANGEE_STORAGE_BACKEND_CLASSES",
    ):
        retired.get_backend_class_impl()


@pytest.mark.django_db
def test_an_update_keeps_a_key_its_row_holds_and_nothing_writes_one_the_row_does_not_hold() -> None:
    """A rename saves, whole or partial, and keeps the key; writing one the row does not hold is refused."""

    retired = _retired_backend("unwritten")
    backend_model = type(retired)
    rows = backend_model._base_manager.filter(pk=retired.pk)

    def choose(key: str) -> Callable[[], None]:
        def write() -> None:
            chosen = backend_model._base_manager.get(pk=retired.pk)
            chosen.backend_class = key
            chosen.save()
        return write

    writes = {
        "choose another": choose("also-unknown"),
        "re-spell the held key": choose("RETIRED"),
        "QuerySet.update": lambda: rows.update(backend_class="also-unknown"),
        "bulk_update": lambda: backend_model._base_manager.bulk_update([retired], ["backend_class"]),
        "insert": lambda: backend_model.objects.create(slug="new", label="New", backend_class="retired"),
    }
    with system_context(reason="test.impl.retired.write"):
        for path, write in writes.items():
            with pytest.raises(ValidationError) as refused, transaction.atomic():
                write()
            assert "backend_class" in refused.value.message_dict, path
        retired.label = "Renamed"
        retired.full_clean()
        retired.save()
        # The update wrote the column as itself; the instance still reads its key, not an expression.
        assert type(retired.backend_class) is str and retired.backend_class == "retired"
        retired.label = "Again"
        retired.save(update_fields=["label"])
        retired.refresh_from_db()
        assert retired.backend_class == "retired"

    stored = backend_model._base_manager.get(pk=retired.pk)
    assert (stored.label, stored.backend_class) == ("Again", "retired")
    assert not backend_model._base_manager.filter(slug="new").exists()


@pytest.mark.django_db
def test_a_model_tracking_its_keys_saves_a_record_whose_key_is_not_registered_and_keeps_its_config() -> None:
    """A create-only selector's row saves its other fields and its config as stored; a changed config is refused."""

    bridge = _retired_bridge("held")
    with system_context(reason="test.impl.retired.mixin"):
        loaded = VcsBridge._base_manager.get(pk=bridge.pk)
        loaded.display_name = "Renamed"
        loaded.full_clean()
        loaded.save()
        loaded.config = {**loaded.config, "added": True}
        with pytest.raises(ValidationError) as refused, transaction.atomic():
            loaded.save()
    held_config = "The configuration cannot change while the backend class is not registered."
    assert refused.value.message_dict == {"config": [held_config]}
    stored = VcsBridge._base_manager.get(pk=bridge.pk)
    assert (stored.display_name, stored.backend_class, stored.config) == ("Renamed", "retired", bridge.config)


@pytest.mark.django_db
def test_a_lookup_reads_spaces_and_a_member_name_as_the_key() -> None:
    """A filter canonicalises once, alike for a key the choices name and for one they no longer name."""

    retired = _retired_backend("canonical")
    backend_model = type(retired)
    with system_context(reason="test.impl.canonical.kept"):
        kept = backend_model.objects.create(slug="canonical-kept", label="Kept", backend_class="local")
    rows = backend_model._base_manager.filter(pk__in=(retired.pk, kept.pk))

    for spelling in ("local", " local ", "LOCAL"):
        assert list(rows.filter(backend_class=spelling)) == [kept]
    assert list(rows.filter(backend_class=" retired ")) == [retired]


@pytest.mark.django_db
def test_a_top_level_value_is_written_as_its_literal_and_any_other_expression_unchecked() -> None:
    """A ``Value``'s literal is a value the field prepares; ``F``, ``Case`` or a ``Value`` inside one is unchecked."""

    backend_model = apps.get_model("storage", "Backend")
    field = backend_model._meta.get_field("backend_class")
    with system_context(reason="test.impl.expression.write"):
        row = backend_model.objects.create(slug="expressed", label="Expressed", backend_class="local")
        rows = backend_model._base_manager.filter(pk=row.pk)
        for refused in (Value("retired"), Value("retired", output_field=field), Value("LOCAL")):
            with pytest.raises(ValidationError) as refusal, transaction.atomic():
                rows.update(backend_class=refused)
            assert refusal.value.message_dict == {"backend_class": [f"Value {refused.value!r} is not a valid choice."]}
        rows.update(backend_class=Value("local"))
        rows.update(backend_class=F("backend_class"))
        assert rows.get().backend_class == "local"
        rows.update(backend_class=Case(When(pk=row.pk, then=Value("retired")), output_field=field))

    assert rows.get().backend_class == "retired"


@pytest.mark.django_db
def test_a_write_stores_only_the_registry_key_as_spelled_while_a_lookup_forgives() -> None:
    """Every write path refuses the member's name and a padded key with the field's refusal; a lookup reads both."""

    backend_model = apps.get_model("storage", "Backend")
    with system_context(reason="test.impl.spelling.write"):
        row = backend_model.objects.create(slug="spelled", label="Spelled", backend_class="local")
        rows = backend_model._base_manager.filter(pk=row.pk)

        def save(spelling: str) -> None:
            row.backend_class = spelling
            row.save()

        def bulk_update(spelling: str) -> None:
            row.backend_class = spelling
            backend_model._base_manager.bulk_update([row], ["backend_class"])

        writes = {
            "create": lambda spelling: backend_model.objects.create(slug="new", label="New", backend_class=spelling),
            "save": save,
            "update": lambda spelling: rows.update(backend_class=spelling),
            "bulk_create": lambda spelling: backend_model._base_manager.bulk_create(
                [backend_model(slug="bulk", label="Bulk", backend_class=spelling)]
            ),
            "bulk_update": bulk_update,
            "update_or_create": lambda spelling: backend_model.objects.update_or_create(
                slug="spelled", defaults={"backend_class": spelling}
            ),
        }
        for spelling in ("LOCAL", " local "):
            for path, write in writes.items():
                with pytest.raises(ValidationError) as refusal, transaction.atomic():
                    write(spelling)
                refused = {"backend_class": [f"Value {spelling!r} is not a valid choice."]}
                assert refusal.value.message_dict == refused, path
        row.refresh_from_db()
        assert [list(rows.filter(backend_class=spelling)) for spelling in ("LOCAL", " local ")] == [[row], [row]]
        rows.update(backend_class="local")

    assert backend_model._base_manager.filter(slug__in=("new", "bulk")).count() == 0
    assert rows.get().backend_class == "local"


@pytest.mark.django_db
def test_a_copy_that_writes_a_retired_key_still_refuses_another_spelling_of_a_registered_one() -> None:
    """A migration's state model and a history copy write a key the choices no longer name, never "LOCAL"."""

    state_model = ProjectState.from_apps(apps).apps.get_model("storage", "Backend")
    owner = create_user("spelled-history-owner")
    vault = vault_for(owner)
    history_rows = type(vault.history.get())._base_manager.filter(id=vault.pk)
    with system_context(reason="test.impl.spelling.exempt"):
        state_row = state_model.objects.create(slug="state", label="State", backend_class="local")
        state_rows = state_model.objects.filter(pk=state_row.pk)
        for rows, field, spelling in (
            (state_rows, "backend_class", "LOCAL"),
            (state_rows, "backend_class", " local "),
            (history_rows, "retrieval_class", "LEXICAL"),
        ):
            with pytest.raises(ValidationError) as refusal, transaction.atomic():
                rows.update(**{field: spelling})
            assert refusal.value.message_dict == {field: [f"Value {spelling!r} is not a valid choice."]}
            rows.update(**{field: "retired"})
            assert rows.values_list(field, flat=True).get() == "retired"


def test_an_implementation_carries_the_config_keys_it_reads_defaults_the_rest_and_drops_the_others() -> None:
    """``carried_config``: a shared key kept, a missing one defaulted, an unreadable one dropped."""

    carried = LocalVCSBackend.carried_config({"local_root": "/srv/team", "retired_option": True})
    assert carried == {
        "local_root": "/srv/team", "local_name": "", "local_org": "local", "local_default_branch": "main",
    }
    assert _BaseImpl.carried_config({"untyped": 1}) == {"untyped": 1}


@pytest.mark.django_db
def test_a_create_only_selector_moves_off_a_key_not_registered_once_carrying_its_config() -> None:
    """A repair saves with its config carried over, then create-only holds; no key is ever dropped silently."""

    bridge = _retired_bridge("repaired")
    with system_context(reason="test.impl.repair"):
        VcsBridge._base_manager.filter(pk=bridge.pk).update(config={"local_root": "/srv/team"})
        row = VcsBridge._base_manager.get(pk=bridge.pk)
        row.backend_class = "local"
        row.save()
        stored = VcsBridge._base_manager.get(pk=bridge.pk)
        assert (stored.backend_class, stored.config["local_root"], stored.config["local_org"]) == (
            "local", "/srv/team", "local",
        )
        stored.backend_class = "github"
        with pytest.raises(ValidationError) as refused, transaction.atomic():
            stored.save()
    assert refused.value.message_dict == {"backend_class": ["Implementation selection is create-only."]}

    dropping = _retired_bridge("dropping")
    with system_context(reason="test.impl.repair.drop"):
        VcsBridge._base_manager.filter(pk=dropping.pk).update(
            config={"local_root": "/srv/team", "retired_option": True},
        )
        row = VcsBridge._base_manager.get(pk=dropping.pk)
        row.backend_class = "local"
        with pytest.raises(ValidationError) as dropped, transaction.atomic():
            row.save()
        assert dropped.value.message_dict == {"config": [
            "Choosing this backend class drops retired_option from the configuration: send the configuration to save.",
        ]}
        row = VcsBridge._base_manager.get(pk=dropping.pk)
        row.backend_class = "local"
        row.config = {"local_root": "/srv/sent"}
        row.save()
        sent = VcsBridge._base_manager.values_list("backend_class", "config__local_root").get(pk=dropping.pk)
        assert sent == ("local", "/srv/sent")


@pytest.mark.django_db
def test_a_row_loaded_through_its_model_answers_holds_from_its_loaded_key_reading_nothing(
    django_assert_num_queries: Any,
) -> None:
    """The loaded key decides; a row built without loading reads the stored column, with the context's audit row."""

    bridge = _retired_bridge("holds-loaded")
    field = VcsBridge._meta.get_field("backend_class")
    with system_context(reason="test.impl.holds"):
        loaded = VcsBridge._base_manager.get(pk=bridge.pk)
    built = VcsBridge(pk=bridge.pk)
    built._state.adding = False
    built._state.db = loaded._state.db

    with django_assert_num_queries(0):
        assert field.holds(loaded, "retired")
        assert not field.holds(loaded, "other")
    with django_assert_num_queries(2):
        assert field.holds(built, "retired")


_CREATE_ONLY_SELECTORS = (
    ("agents", "InferenceProvider", "backend_class"),
    ("calendar", "CalendarSource", "backend_class"),
    ("integrate_vcs", "VcsBridge", "backend_class"),
    ("knowledge_integrate_notion", "NotionConnection", "backend_class"),
    ("messaging", "Channel", "backend_class"),
    ("parties", "Directory", "backend_class"),
    ("posts", "Feed", "feed_backend_class"),
    ("storage_integrate", "Mount", "backend_class"),
)


@pytest.mark.django_db
@pytest.mark.parametrize(("app_label", "model_name", "selector"), _CREATE_ONLY_SELECTORS)
def test_each_create_only_selector_is_repaired_once_then_create_only(
    app_label: str, model_name: str, selector: str,
) -> None:
    """Through each model's own class: a rename keeps the retired key, a repair saves once, then create-only holds."""

    model = apps.get_model(app_label, model_name)
    field = model._meta.get_field(selector)
    first, *others = field.registered_keys()
    extra = {"drive": _a_drive()} if model_name == "Mount" else {}
    row = make_integration(f"repair-{model_name.lower()}", model=model, **{selector: first}, **extra)
    store_column(model, row.pk, field.column, "retired")
    with system_context(reason="test.impl.repair.each"):
        renamed = model._base_manager.get(pk=row.pk)
        renamed.display_name = "Renamed"
        renamed.save()
        repaired = model._base_manager.get(pk=row.pk)
        assert getattr(repaired, field.attname) == "retired"
        setattr(repaired, field.attname, first)
        repaired.save()
        assert model._base_manager.values_list(field.attname, flat=True).get(pk=row.pk) == first
        again = model._base_manager.get(pk=row.pk)
        setattr(again, field.attname, others[0] if others else "retired")
        with pytest.raises(ValidationError), transaction.atomic():
            again.save()
    assert model._base_manager.values_list(field.attname, flat=True).get(pk=row.pk) == first


@pytest.mark.django_db
def test_a_create_only_selector_holding_a_registered_key_is_as_create_only_as_before() -> None:
    """No repair is offered off a registered key: the change is refused and the stored key stays."""

    bridge = make_integration("registered", model=VcsBridge, backend_class="local")
    with system_context(reason="test.impl.repair.registered"):
        row = VcsBridge._base_manager.get(pk=bridge.pk)
        row.backend_class = "stub"
        with pytest.raises(ValidationError) as refused, transaction.atomic():
            row.save()
    assert refused.value.message_dict == {"backend_class": ["Implementation selection is create-only."]}
    assert VcsBridge._base_manager.values_list("backend_class", flat=True).get(pk=bridge.pk) == "local"


@pytest.mark.django_db
def test_history_recorded_under_a_key_retired_later_loads_displays_and_keeps_recording_it() -> None:
    """History loads under the retired key, shows it and keeps recording it; the live row refuses another one."""

    owner = create_user("retired-history-owner")
    vault = vault_for(owner)
    with actor_context(owner), changeset(fresh=True, change_reason="Rename vault"):
        vault.name = "Renamed"
        vault.save(update_fields=("name",))
    vault_model = type(vault)
    first, renamed = vault.history.order_by("history_id")
    store_column(type(first), first.history_id, "retrieval_class", "retired")
    store_column(vault_model, vault.pk, "retrieval_class", "retired")
    with (
        actor_context(owner),
        pytest.raises(ValidationError) as refused,
        transaction.atomic(),
        changeset(fresh=True, change_reason="Refused"),
    ):
        chosen = vault_model._base_manager.get(pk=vault.pk)
        chosen.retrieval_class = "also-retired"
        chosen.save()
    assert "retrieval_class" in refused.value.message_dict

    history = model_history(vault_model)
    assert history is not None
    changes = {change.field: (change.old, change.new) for change in history.diff(renamed, renamed).changes}
    assert changes["retrieval_class"] == ("retired", "lexical")
    with actor_context(owner), changeset(fresh=True, change_reason="Rename again"):
        stored = vault_model._base_manager.get(pk=vault.pk)
        stored.name = "Again"
        stored.save(update_fields=("name",))
    assert [version.retrieval_class for version in vault.history.order_by("history_id")] == [
        "retired", "lexical", "retired",
    ]


@pytest.mark.django_db(transaction=True)
@isolate_apps()
def test_a_release_whose_registry_lacks_a_key_reads_and_saves_its_rows_and_refuses_to_resolve_or_insert_it() -> None:
    """Two releases share one table in a rolling deploy; each field's choices are its own release's registry."""

    both = {"base": "tests.test_impl._BaseImpl", "refined": "tests.test_impl._RefinedImpl"}
    older = {"base": "tests.test_impl._BaseImpl"}
    with override_settings(ANGEE_TEST_IMPLS=both):

        class NewRelease(models.Model):
            adapter = ImplClassField(_BaseImpl)
            label = models.CharField(max_length=20, default="")

            class Meta:
                app_label = "tests"
                db_table = "test_impl_rolling_release"

    with override_settings(ANGEE_TEST_IMPLS=older):

        class OldRelease(models.Model):
            adapter = ImplClassField(_BaseImpl)
            label = models.CharField(max_length=20, default="")

            class Meta:
                app_label = "tests"
                db_table = "test_impl_rolling_release"

    with model_tables((NewRelease,)):
        with override_settings(ANGEE_TEST_IMPLS=both):
            written = NewRelease.objects.create(adapter="refined", label="new")
            NewRelease.objects.create(adapter="base", label="shared")
        with override_settings(ANGEE_TEST_IMPLS=older):
            row = OldRelease.objects.get(pk=written.pk)
            assert type(row.adapter) is str and row.adapter == "refined"
            assert sorted(OldRelease.objects.values_list("adapter", flat=True)) == ["base", "refined"]
            assert list(OldRelease.objects.filter(adapter="refined")) == [row]
            with pytest.raises(
                ImproperlyConfigured,
                match=r"^tests\.OldRelease\.adapter: No impl for key 'refined' in settings\.ANGEE_TEST_IMPLS ",
            ):
                row.get_adapter_impl()
            with pytest.raises(ValidationError, match="Value 'refined' is not a valid choice"):
                OldRelease.objects.create(adapter="refined")
            row.label = "renamed"
            row.save()
        with override_settings(ANGEE_TEST_IMPLS=both):
            current = NewRelease.objects.get(pk=written.pk)
            assert (current.label, current.get_adapter_impl()) == ("renamed", _RefinedImpl)
