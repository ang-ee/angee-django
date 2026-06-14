"""Tests for plain Django AppConfig based Angee contracts."""

from __future__ import annotations

from types import ModuleType

import pytest
from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured

from angee.base.apps import BaseConfig
from angee.graphql.schema import schema_parts_for
from angee.resources.entries import ResourceEntry, resource_manifest_for


def _module(name: str) -> ModuleType:
    """Return a synthetic module with a Django app filesystem path."""

    module = ModuleType(name)
    module.__file__ = __file__
    return module


def test_base_config_is_a_dependency_node() -> None:
    """The model foundation participates in addon dependency ordering."""

    base = apps.get_app_config("base")

    assert isinstance(base, BaseConfig)
    assert base.depends_on == (
        "angee.compose",
        "django.contrib.contenttypes",
        "rebac",
        "reversion",
        "simple_history",
    )


def test_resource_manifest_normalizes_tiers_and_entries() -> None:
    """Resource declarations normalize in the resource subsystem."""

    class ResourceConfig(AppConfig):
        name = "tests.resources"
        label = "test_resources"
        resources = {
            "install": (
                "resources/users.csv",
                {
                    "path": "resources/notes.yaml",
                    "depends_on": ["resources/users.csv"],
                    "adopt": True,
                },
                {
                    "path": "resources/comments.yaml",
                    "depends_on": "resources/notes.yaml",
                },
            ),
            "demo": {"url": "https://example.test/demo.csv"},
        }

    config = ResourceConfig("tests.resources", _module("tests.resources"))
    manifest = resource_manifest_for(config)

    assert manifest["master"] == ()
    assert manifest["install"] == (
        {"path": "resources/users.csv"},
        {
            "path": "resources/notes.yaml",
            "depends_on": ("resources/users.csv",),
            "adopt": True,
        },
        {
            "path": "resources/comments.yaml",
            "depends_on": ("resources/notes.yaml",),
        },
    )
    assert manifest["demo"] == ({"url": "https://example.test/demo.csv"},)


def test_iam_config_installs_public_oauth_provider_resources() -> None:
    """IAM ships public OAuth provider resources without deployment secrets."""

    config = apps.get_app_config("iam")
    manifest = resource_manifest_for(config)

    assert manifest["install"] == (
        {"path": "resources/install/010_iam.oauthclient.yaml", "adopt": ("slug", "environment")},
    )

    entry = ResourceEntry.from_declaration(config, "install", manifest["install"][0])
    rows = entry.read_resource_rows()

    assert {row.xref for row in rows} == {"anthropic", "gemini", "grok"}
    assert {row.model_label for row in rows} == {"iam.OAuthClient"}
    assert {row.values["slug"] for row in rows} == {"anthropic", "gemini", "grok"}
    rows_by_xref = {row.xref: row for row in rows}
    anthropic = rows_by_xref["anthropic"].values
    assert anthropic["environment"] == "prod"
    assert anthropic["client_id"] == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    assert anthropic["authorize_endpoint"] == "https://platform.claude.com/oauth/authorize"
    assert anthropic["token_endpoint"] == "https://platform.claude.com/v1/oauth/token"
    assert anthropic["userinfo_endpoint"] == "https://api.anthropic.com/api/oauth/profile"
    assert anthropic["token_request_format"] == "json"
    assert anthropic["authorize_params"] == {"code": "true"}
    assert anthropic["default_scopes"] == [
        "user:profile",
        "user:inference",
        "org:create_api_key",
        "user:sessions:claude_code",
        "user:mcp_servers",
        "user:file_upload",
    ]
    assert anthropic["external_id_claim"] == "account.uuid"
    assert anthropic["email_claim"] == "account.email_address"
    assert "client_secret" not in anthropic
    gemini = rows_by_xref["gemini"].values
    assert gemini["environment"] == "prod"
    assert gemini["client_id"] == "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
    assert gemini["issuer"] == "https://accounts.google.com"
    assert gemini["authorize_endpoint"] == "https://accounts.google.com/o/oauth2/v2/auth"
    assert gemini["token_endpoint"] == "https://oauth2.googleapis.com/token"
    assert gemini["userinfo_endpoint"] == "https://www.googleapis.com/oauth2/v2/userinfo"
    assert gemini["jwks_uri"] == "https://www.googleapis.com/oauth2/v3/certs"
    assert gemini["discovery_url"] == "https://accounts.google.com/.well-known/openid-configuration"
    assert gemini["is_enabled"] is True
    assert gemini["is_oidc"] is True
    assert gemini["authorize_params"] == {"access_type": "offline"}
    assert gemini["default_scopes"] == [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
    assert gemini["display_name_claim"] == "name"
    assert gemini["avatar_url_claim"] == "picture"
    assert "client_secret" not in gemini

    grok = rows_by_xref["grok"].values
    assert grok["environment"] == "prod"
    assert grok["client_id"] == "b1a00492-073a-47ea-816f-4c329264a828"
    assert grok["issuer"] == "https://auth.x.ai"
    assert grok["authorize_endpoint"] == "https://auth.x.ai/oauth2/authorize"
    assert grok["token_endpoint"] == "https://auth.x.ai/oauth2/token"
    assert grok["discovery_url"] == "https://auth.x.ai/.well-known/openid-configuration"
    assert grok["is_enabled"] is False
    assert grok["is_oidc"] is True
    assert grok["authorize_params"] == {"plan": "generic"}
    assert grok["default_scopes"] == [
        "openid",
        "profile",
        "email",
        "offline_access",
        "grok-cli:access",
        "api:access",
    ]
    assert "client_secret" not in grok


def test_integrate_config_installs_agentic_vendor_resources() -> None:
    """Integrate ships vendor rows for OAuth-backed agentic credentials."""

    config = apps.get_app_config("integrate")
    manifest = resource_manifest_for(config)

    assert manifest["master"] == (
        {"path": "resources/master/010_integrate.vendor.yaml", "adopt": "slug"},
    )

    entry = ResourceEntry.from_declaration(config, "master", manifest["master"][0])
    rows = entry.read_resource_rows()
    rows_by_xref = {row.xref: row for row in rows}

    assert {"anthropic", "gemini", "grok"} <= set(rows_by_xref)
    assert rows_by_xref["gemini"].values["slug"] == "gemini"
    assert rows_by_xref["gemini"].values["display_name"] == "Google Gemini"
    assert rows_by_xref["grok"].values["slug"] == "grok"
    assert rows_by_xref["grok"].values["website_url"] == "https://x.ai/grok"


def test_resource_manifest_rejects_unknown_tiers() -> None:
    """Only resource tiers owned by the resource subsystem are accepted."""

    class BrokenConfig(AppConfig):
        name = "tests.broken_resources"
        label = "broken_resources"
        resources = {"fixture": ("resources/fixture.csv",)}

    config = BrokenConfig(
        "tests.broken_resources",
        _module("tests.broken_resources"),
    )

    with pytest.raises(ImproperlyConfigured, match="Unknown resource tier"):
        resource_manifest_for(config)


def _config_with_schemas(schemas: object) -> AppConfig:
    """Return an app config whose ``schemas`` attribute carries a declaration."""

    config = AppConfig("tests.graphql", _module("tests.graphql"))
    config.schemas = schemas
    return config


def test_get_schema_parts_normalizes_scalars_and_buckets() -> None:
    """A scalar contribution becomes a one-tuple; absent buckets are empty."""

    sentinel = object()
    config = _config_with_schemas({"public": {"query": sentinel}})
    parts = schema_parts_for(config)

    assert parts["public"].query == (sentinel,)
    assert parts["public"].mutation == ()


def test_get_schema_parts_rejects_unknown_keys() -> None:
    """An unknown merge bucket fails fast."""

    config = _config_with_schemas({"public": {"queries": []}})
    with pytest.raises(ImproperlyConfigured, match="unknown keys: queries"):
        schema_parts_for(config)


def test_get_schema_parts_rejects_sets() -> None:
    """Unordered sets are rejected so builds stay deterministic."""

    config = _config_with_schemas({"public": {"query": {object()}}})
    with pytest.raises(ImproperlyConfigured, match="not a set"):
        schema_parts_for(config)


def test_get_schema_parts_missing_module_is_empty() -> None:
    """An addon without a schema module contributes nothing."""

    config = AppConfig("tests.no_schema", _module("tests.no_schema"))

    assert schema_parts_for(config) == {}


def test_config_attributes_are_owned_by_consumers() -> None:
    """Config attributes are explicit declarations owned by consumers."""

    class ManifestOnlyConfig(AppConfig):
        name = "tests.manifest_only"
        label = "manifest_only"
        schemas: dict[str, object] = {}
        resources: dict[str, object] = {}

    class DependencyNodeConfig(AppConfig):
        name = "tests.marked_addon"
        label = "marked_addon"
        angee_addon = True
        depends_on = ()

    manifest_only = ManifestOnlyConfig(
        "tests.manifest_only",
        _module("tests.manifest_only"),
    )
    dependency_node = DependencyNodeConfig("tests.marked_addon", _module("tests.marked_addon"))

    assert manifest_only.schemas == {}
    assert manifest_only.resources == {}
    assert dependency_node.angee_addon is True
    assert dependency_node.depends_on == ()
