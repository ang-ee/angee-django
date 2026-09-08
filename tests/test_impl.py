"""Tests for the framework ImplBase: default inheritance, choice metadata, materialise."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from enum import Enum
from typing import Literal

import pytest
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models
from django.test import override_settings
from pydantic import BaseModel, Field

from angee.base.impl import ImplBase, ImplChoice, ImplClassField
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
        resolve_impl_class,
    )

    assert ImplBase.__name__ == "ImplBase"
    assert ImplChoice.__name__ == "ImplChoice"
    assert ImplClassField.__name__ == "ImplClassField"
    assert ImplDefaultsMixin.__name__ == "ImplDefaultsMixin"
    assert callable(impl_registry)
    assert callable(resolve_impl_class)
    legacy_modules = ("impl_types", "registry")
    for legacy_module in legacy_modules:
        assert importlib.util.find_spec(f"angee.base.{legacy_module}") is None


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


def test_model_impl_field_is_the_public_declared_accessor() -> None:
    """Models expose their impl field through the declared public seam."""

    with pytest.raises(FieldDoesNotExist, match="Integration.lifecycle is not an ImplClassField"):
        Integration.impl_field("lifecycle")
    assert not hasattr(Integration, "_impl_field")


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
    assert calls == 0


@pytest.mark.parametrize(
    ("annotation", "detail"),
    [
        (str | int, "union"),
        (dict[str, str], "additionalProperties"),
        (tuple[str, int], "prefixItems"),
    ],
)
def test_typed_config_rejects_unsupported_shapes_with_exact_path(annotation: object, detail: str) -> None:
    """A shape FormSpec cannot preserve fails at the declaring field path."""

    UnsupportedConfig = type(
        "UnsupportedConfig",
        (BaseModel,),
        {"__annotations__": {"payload": annotation}},
    )

    class UnsupportedImpl(ImplBase):
        config_model = UnsupportedConfig

    with pytest.raises(ImproperlyConfigured, match=rf"UnsupportedImpl.*config\.payload.*{detail}"):
        UnsupportedImpl.config_form_spec()


def test_typed_config_rejects_recursive_models_and_nested_aliases() -> None:
    """References must be finite and field names must have one wire identity."""

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

    with pytest.raises(ImproperlyConfigured, match=r"AliasedImpl.*config\.child\.value.*aliases"):
        AliasedImpl.config_form_spec()


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
