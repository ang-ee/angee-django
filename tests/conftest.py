"""Shared pytest infrastructure for source-addon IAM tests."""

from __future__ import annotations

from django.db import connection, models

from angee.iam.models import Credential as AbstractCredential
from angee.iam.models import ExternalAccount as AbstractExternalAccount
from angee.iam.models import OAuthClient as AbstractOAuthClient
from angee.iam.models import Vendor as AbstractVendor


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


def _create_missing_tables() -> list[type[models.Model]]:
    """Create canonical IAM connection test tables when pytest did not sync them."""

    existing_tables = set(connection.introspection.table_names())
    test_models = [Vendor, ExternalAccount, OAuthClient, Credential]
    missing = [model for model in test_models if model._meta.db_table not in existing_tables]
    if not missing:
        return []
    with connection.schema_editor() as schema_editor:
        for model in missing:
            schema_editor.create_model(model)
    return missing
