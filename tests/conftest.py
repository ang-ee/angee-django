"""Shared pytest infrastructure for source-addon tests."""

from __future__ import annotations

from collections.abc import Iterator
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
import reversion
from django.apps import AppConfig
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection, models
from django.test import RequestFactory
from rebac import actor_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.iam.models import Credential as AbstractCredential
from angee.iam.models import ExternalAccount as AbstractExternalAccount
from angee.iam.models import OAuthClient as AbstractOAuthClient
from angee.iam.models import Vendor as AbstractVendor
from angee.integrate.models import WebhookSubscription as AbstractWebhookSubscription
from angee.knowledge.models import MarkdownPage as AbstractMarkdownPage
from angee.knowledge.models import Page as AbstractPage
from angee.knowledge.models import Vault as AbstractVault


class Vendor(AbstractVendor):
    """Concrete IAM vendor used by tests that run without composed runtime models."""

    class Meta(AbstractVendor.Meta):
        """Django model options for the canonical test vendor."""

        abstract = False
        app_label = "iam"
        db_table = "test_connections_vendor"
        rebac_resource_type = "auth/vendor"
        rebac_id_attr = "sqid"


class ExternalAccount(AbstractExternalAccount):
    """Concrete IAM external account used by source-addon tests."""

    class Meta(AbstractExternalAccount.Meta):
        """Django model options for the canonical test external account."""

        abstract = False
        app_label = "iam"
        db_table = "test_connections_external_account"
        rebac_resource_type = "auth/external_account"
        rebac_id_attr = "sqid"


class OAuthClient(AbstractOAuthClient):
    """Concrete IAM OAuth client used by source-addon tests."""

    class Meta(AbstractOAuthClient.Meta):
        """Django model options for the canonical test OAuth client."""

        abstract = False
        app_label = "iam"
        db_table = "test_connections_oauth_client"
        rebac_resource_type = "auth/oauth_client"
        rebac_id_attr = "sqid"


class Credential(AbstractCredential):
    """Concrete IAM credential used by source-addon tests."""

    class Meta(AbstractCredential.Meta):
        """Django model options for the canonical test credential."""

        abstract = False
        app_label = "iam"
        db_table = "test_connections_credential"
        rebac_resource_type = "auth/credential"
        rebac_id_attr = "sqid"


class WebhookSubscription(AbstractWebhookSubscription):
    """Concrete integrate webhook subscription used by source-addon tests."""

    class Meta(AbstractWebhookSubscription.Meta):
        """Django model options for the canonical test webhook subscription."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_webhook_subscription"
        rebac_resource_type = "integrate/webhook_subscription"
        rebac_id_attr = "sqid"


class Vault(AbstractVault):
    """Concrete knowledge vault used by source-addon tests."""

    class Meta(AbstractVault.Meta):
        """Django model options for the canonical test vault."""

        abstract = False
        app_label = "knowledge"
        db_table = "test_knowledge_vault"
        rebac_resource_type = "knowledge/vault"
        rebac_id_attr = "sqid"


class Page(AbstractPage):
    """Concrete knowledge page used by source-addon tests."""

    class Meta(AbstractPage.Meta):
        """Django model options for the canonical test page."""

        abstract = False
        app_label = "knowledge"
        db_table = "test_knowledge_page"
        rebac_resource_type = "knowledge/page"
        rebac_id_attr = "sqid"


@reversion.register(fields=("body",))
class MarkdownPage(AbstractMarkdownPage):
    """Concrete knowledge markdown sidecar used by source-addon tests.

    Registered with django-reversion the way the composer registers the
    emitted runtime model (``RevisionMixin.angee_model_decorators``).
    """

    class Meta(AbstractMarkdownPage.Meta):
        """Django model options for the canonical test markdown page."""

        abstract = False
        app_label = "knowledge"
        db_table = "test_knowledge_markdown_page"
        rebac_resource_type = "knowledge/markdown_page"
        rebac_id_attr = "sqid"


IAM_CONNECTION_TEST_MODELS = (Vendor, ExternalAccount, OAuthClient, Credential)
"""Concrete IAM connection models created on demand by IAM test fixtures."""

KNOWLEDGE_TEST_MODELS = (Vault, Page, MarkdownPage)
"""Concrete knowledge models created on demand by knowledge test fixtures."""


def _create_missing_tables(
    test_models: tuple[type[models.Model], ...] = IAM_CONNECTION_TEST_MODELS,
) -> list[type[models.Model]]:
    """Create concrete source-addon test tables when pytest did not sync them."""

    existing_tables = set(connection.introspection.table_names())
    missing = [model for model in test_models if model._meta.db_table not in existing_tables]
    if not missing:
        return []
    with connection.schema_editor() as schema_editor:
        for model in missing:
            schema_editor.create_model(model)
    return missing


@pytest.fixture()
def knowledge_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete knowledge tables and sync the REBAC schema."""

    del transactional_db
    created_models = _create_missing_tables(KNOWLEDGE_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def create_user(username: str) -> Any:
    """Create one plain test user."""

    return get_user_model().objects.create_user(username=username, password=username)


def vault_for(owner: Any, *, name: str = "Research") -> Any:
    """Create a vault owned by ``owner`` through the actor-scoped factory."""

    with actor_context(owner):
        return Vault.objects.create_for(owner, name=name)


class SchemaAddon(AppConfig):
    """Small addon stand-in exposing raw GraphQL schema declarations."""

    def __init__(self, schemas: dict[str, dict[str, tuple[object, ...]]]) -> None:
        module = ModuleType("tests.graphql_addon")
        module.__file__ = __file__
        super().__init__("tests.graphql_addon", module)
        self.schemas = schemas


def addon_schema(schemas: dict[str, Any], name: str) -> Any:
    """Build one addon-only GraphQL schema from its raw ``schemas`` mapping."""

    parts = {key: tuple(schemas[name].get(key, ())) for key in SCHEMA_PART_KEYS}
    return GraphQLSchemas([SchemaAddon({name: parts})]).build(name)


def graphql_request(user: Any) -> Any:
    """Return a bare POST request carrying ``user``."""

    request = RequestFactory().post("/graphql/public/")
    request.user = user
    return request


def execute_schema(
    schema: Any,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    user: Any | None = None,
    request: Any | None = None,
) -> Any:
    """Execute a GraphQL operation with a request-shaped context."""

    request = request or graphql_request(user or AnonymousUser())
    actor = getattr(request, "user", AnonymousUser())
    with actor_context(actor):
        return schema.execute_sync(
            query,
            variable_values=variables or {},
            context_value=SimpleNamespace(request=request),
        )


def result_data(result: Any) -> dict[str, Any]:
    """Return result data after asserting the operation succeeded."""

    assert result.errors is None, result.errors
    assert result.data is not None
    return cast(dict[str, Any], result.data)
