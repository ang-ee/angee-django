"""Tests for the framework ImplBase: default inheritance, choice metadata, materialise."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal

import pytest
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import connection, models
from django.test import override_settings
from django.test.utils import isolate_apps
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, PlainSerializer

from angee.base.impl import (
    ImplBase,
    ImplChoice,
    ImplClassField,
    ImplDefaultsMixin,
    model_config_form_spec,
    resolve_all_impl_classes,
)
from angee.workflows.configs import EmitConfig, JoinContinuationConfig
from tests.conftest import Integration, OAuthClient, VcsBridge


class _BaseImpl(ImplBase):
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


class _TypedConfigImpl(ImplBase):
    key = "typed"
    config_model = _ScalarConfig


def test_impl_owner_public_import_contract() -> None:
    """The impl mechanism has one public owner and no split legacy modules."""

    from angee.base.impl import (  # noqa: PLC0415
        ImplBase,
        ImplChoice,
        ImplClassField,
        ImplDefaultsMixin,
        impl_registry,
        resolve_all_impl_classes,
        resolve_impl_class,
    )

    assert ImplBase.__name__ == "ImplBase"
    assert ImplChoice.__name__ == "ImplChoice"
    assert ImplClassField.__name__ == "ImplClassField"
    assert ImplDefaultsMixin.__name__ == "ImplDefaultsMixin"
    assert callable(impl_registry)
    assert callable(resolve_all_impl_classes)
    assert callable(resolve_impl_class)
    legacy_modules = ("impl_types", "registry")
    for legacy_module in legacy_modules:
        assert importlib.util.find_spec(f"angee.base.{legacy_module}") is None


@override_settings(
    ANGEE_TEST_IMPLS={
        "refined": "tests.test_impl._RefinedImpl",
        "base": "tests.test_impl._BaseImpl",
    }
)
def test_resolve_all_impl_classes_is_sorted_and_validates_keys() -> None:
    """Registry enumeration is deterministic and owns declaration-key agreement."""

    assert resolve_all_impl_classes("ANGEE_TEST_IMPLS", _BaseImpl) == (
        _BaseImpl,
        _RefinedImpl,
    )

    with (
        override_settings(ANGEE_TEST_IMPLS={"wrong": "tests.test_impl._RefinedImpl"}),
        pytest.raises(ImproperlyConfigured, match="with key 'refined'"),
    ):
        resolve_all_impl_classes("ANGEE_TEST_IMPLS", _BaseImpl)


@override_settings(
    ANGEE_TEST_IMPLS={
        "base": "tests.test_impl._BaseImpl",
        "missing": "tests.test_impl.MissingImpl",
        "wrong_base": "builtins.str",
    }
)
def test_resolve_all_impl_classes_can_collect_every_registry_fault() -> None:
    """System-check callers receive all failures without duplicating resolution."""

    faults: list[tuple[str, Exception]] = []

    assert resolve_all_impl_classes(
        "ANGEE_TEST_IMPLS",
        _BaseImpl,
        on_error=lambda key, error: faults.append((key, error)),
    ) == (_BaseImpl,)
    assert [key for key, _error in faults] == ["missing", "wrong_base"]
    assert isinstance(faults[0][1], ImportError)
    assert isinstance(faults[1][1], ImproperlyConfigured)


@override_settings(ANGEE_EMPTY_IMPLS={})
def test_historical_impl_field_reconstructs_without_removed_registry() -> None:
    """Serialized migration fields retain their stored default after their registry is removed."""

    with pytest.raises(ImproperlyConfigured, match="registry .* is empty"):
        ImplClassField(
            base_class=ImplBase,
            registry_setting="ANGEE_EMPTY_IMPLS",
            default="none",
        )

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


def test_model_impl_field_is_the_public_declared_accessor() -> None:
    """Models expose their impl field through the declared public seam."""

    with pytest.raises(FieldDoesNotExist, match="Integration.lifecycle is not an ImplClassField"):
        Integration.impl_field("lifecycle")
    assert not hasattr(Integration, "_impl_field")


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TEST_IMPLS={"typed": "tests.test_impl._TypedConfigImpl"})
@isolate_apps()
def test_base_validation_refreshes_and_normalizes_deferred_config() -> None:
    """Inherited validation refreshes deferred config before normalizing a write."""

    class DeferredConfigRecord(ImplDefaultsMixin):
        adapter = ImplClassField(base_class=ImplBase, registry_setting="ANGEE_TEST_IMPLS", create_only=True)
        config = models.JSONField(default=dict)

        class Meta:
            app_label = "tests"

    with connection.schema_editor() as editor:
        editor.create_model(DeferredConfigRecord)
    try:
        record = DeferredConfigRecord.objects.create(adapter="typed", config={"retries": 1})
        DeferredConfigRecord.objects.filter(pk=record.pk).update(config={"retries": "3"})
        deferred = DeferredConfigRecord.objects.only("pk").get(pk=record.pk)
        # Pin the deferred setup whose explicit config write must still validate.
        assert deferred.get_deferred_fields() == {"adapter", "config"}

        deferred.save(update_fields={"config"})
        stored = DeferredConfigRecord.objects.get(pk=record.pk)

        assert stored.config == {"endpoint": "https://example.test", "retries": 3}
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(DeferredConfigRecord)


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TEST_IMPLS={"typed": "tests.test_impl._TypedConfigImpl"})
@isolate_apps()
def test_impl_save_leaves_untouched_config_and_selector_deferred(django_assert_num_queries) -> None:
    """An unrelated save neither reads nor rewrites the deferred config and selector."""

    class DeferredConfigRecord(ImplDefaultsMixin):
        adapter = ImplClassField(base_class=ImplBase, registry_setting="ANGEE_TEST_IMPLS", create_only=True)
        config = models.JSONField(default=dict)
        label = models.CharField(max_length=50)

        class Meta:
            app_label = "tests"

    with connection.schema_editor() as editor:
        editor.create_model(DeferredConfigRecord)
    try:
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
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(DeferredConfigRecord)


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


def test_typed_config_projects_native_date_widgets() -> None:
    """Pydantic date formats use the existing FormSpec date controls."""

    class DateConfig(BaseModel):
        day: date
        moment: datetime

    class DateImpl(ImplBase):
        config_model = DateConfig

    spec = DateImpl.config_form_spec()
    assert spec is not None
    assert spec["properties"]["day"]["widget"] == "date"
    assert spec["properties"]["moment"]["widget"] == "datetime"


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

    spec = model_config_form_spec(RelatedConfig, owner="RelatedConfig")
    assert spec["properties"]["target_id"]["relation"] == relation

    class InvalidFilterConfig(BaseModel):
        target_id: str = Field(json_schema_extra={"relation": {
            "resource": "demo.Target",
            "filters": [{"field": "status", "operator": "approximately", "value": "active"}],
        }})

    with pytest.raises(ImproperlyConfigured, match="invalid relation"):
        model_config_form_spec(InvalidFilterConfig, owner="InvalidFilterConfig")

    class InvalidKindConfig(BaseModel):
        target_ids: list[str] = Field(json_schema_extra={"relation": {"resource": "demo.Target"}})

    with pytest.raises(ImproperlyConfigured, match="relation on a non-string field"):
        model_config_form_spec(InvalidKindConfig, owner="InvalidKindConfig")

    class InvalidWidgetConfig(BaseModel):
        target_id: str = Field(json_schema_extra={
            "widget": "text", "relation": {"resource": "demo.Target"},
        })

    with pytest.raises(ImproperlyConfigured, match="relation with a non-relation widget"):
        model_config_form_spec(InvalidWidgetConfig, owner="InvalidWidgetConfig")


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


def test_workflow_id_paths_project_as_lists_of_strings() -> None:
    """Emit artifacts and continuation joins use the native string-list form shape."""

    emit_spec = model_config_form_spec(EmitConfig, owner="EmitStep")
    join_spec = model_config_form_spec(JoinContinuationConfig, owner="JoinContinuation")

    artifact_id_path = emit_spec["properties"]["artifacts"]["items"]["properties"]["id_path"]
    child_id_path = join_spec["properties"]["child_id_path"]
    for path_spec in (artifact_id_path, child_id_path):
        assert path_spec["type"] == "array"
        assert path_spec["widget"] == "list"
        assert path_spec["minItems"] == 1
        assert path_spec["items"] == {"type": "string", "minLength": 1}
        assert path_spec["presenceRequired"] is True


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
    changed = _RefinedImpl.materialize(client, provided=frozenset({"authorize_endpoint"}))
    assert client.authorize_endpoint == "https://kept/authorize"  # supplied → kept
    assert client.token_endpoint == "https://refined/token"  # unsupplied → seeded
    assert client.userinfo_endpoint == "https://refined/userinfo"  # unsupplied → seeded
    assert changed == {"token_endpoint", "userinfo_endpoint"}


def test_materialize_seeds_boolean_default_when_unprovided() -> None:
    """A boolean default lands when omitted (the create-path fix), not just blank scalars."""

    client = OAuthClient()  # login_enabled model default is False
    _BoolImpl.materialize(client, provided=frozenset())
    assert client.login_enabled is True


def test_materialize_keeps_explicit_value_equal_to_default() -> None:
    """A supplied value is never overwritten, even when it equals the model default."""

    client = OAuthClient(login_enabled=False)
    changed = _BoolImpl.materialize(client, provided=frozenset({"login_enabled"}))
    assert client.login_enabled is False  # caller's explicit False survives the impl's True
    assert changed == set()


def test_post_construction_ingress_can_mark_explicit_fields() -> None:
    """Structured loaders preserve values assigned after Django constructed the row."""

    client = OAuthClient()
    client.login_enabled = False
    client.mark_impl_provided_fields({"login_enabled"})

    changed = _BoolImpl.materialize(client, provided=client._impl_provided_fields)

    assert client.login_enabled is False
    assert changed == set()


def test_materialize_deep_copies_mutable_defaults() -> None:
    """Each row gets its own copy of a dict default — never the shared class object."""

    first = OAuthClient()
    second = OAuthClient()
    _ConfigBase.materialize(first, provided=frozenset())
    _ConfigBase.materialize(second, provided=frozenset())
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

    class NeedsNoSlug(models.Model):
        """Model receiving an FK impl default."""

        target = models.ForeignKey(NoSlug, on_delete=models.CASCADE)

        class Meta:
            app_label = "tests"

    class _BrokenFkImpl(ImplBase):
        key = "broken"
        defaults = {"target": "missing"}

    with pytest.raises(FieldDoesNotExist):
        _BrokenFkImpl.materialize(NeedsNoSlug(), provided=frozenset())
