"""Shared pytest infrastructure for source-addon tests.

Source models are abstract; the concrete classes here materialize them for
tests that run without composed runtime models.
"""

from __future__ import annotations

from django.db import connection, models

from angee.iam.models import Credential as AbstractCredential
from angee.iam.models import ExternalAccount as AbstractExternalAccount
from angee.iam.models import OAuthClient as AbstractOAuthClient
from angee.iam.models import Vendor as AbstractVendor
from angee.integrate.models import WebhookSubscription as AbstractWebhookSubscription
from angee.storage.models import Backend as AbstractStorageBackend
from angee.storage.models import Drive as AbstractDrive
from angee.storage.models import File as AbstractFile
from angee.storage.models import Folder as AbstractFolder
from angee.storage.models import MimeType as AbstractMimeType


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


class Backend(AbstractStorageBackend):
    """Concrete storage backend used by source-addon tests."""

    class Meta(AbstractStorageBackend.Meta):
        """Django model options for the canonical test storage backend."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_backend"
        rebac_resource_type = "storage/backend"
        rebac_id_attr = "sqid"


class Drive(AbstractDrive):
    """Concrete storage drive used by source-addon tests."""

    class Meta(AbstractDrive.Meta):
        """Django model options for the canonical test drive."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_drive"
        rebac_resource_type = "storage/drive"
        rebac_id_attr = "sqid"


class Folder(AbstractFolder):
    """Concrete storage folder used by source-addon tests."""

    class Meta(AbstractFolder.Meta):
        """Django model options for the canonical test folder."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_folder"
        rebac_resource_type = "storage/folder"
        rebac_id_attr = "sqid"


class MimeType(AbstractMimeType):
    """Concrete MIME type used by source-addon tests."""

    class Meta(AbstractMimeType.Meta):
        """Django model options for the canonical test MIME type."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_mimetype"


class File(AbstractFile):
    """Concrete storage file used by source-addon tests."""

    class Meta(AbstractFile.Meta):
        """Django model options for the canonical test file."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_file"
        rebac_resource_type = "storage/file"
        rebac_id_attr = "sqid"


def _create_missing_tables(
    test_models: list[type[models.Model]] | None = None,
) -> list[type[models.Model]]:
    """Create canonical test tables when pytest did not sync them.

    Defaults to the IAM connection models; storage tests pass their own list.
    """

    targets = test_models if test_models is not None else [Vendor, ExternalAccount, OAuthClient, Credential]
    existing_tables = set(connection.introspection.table_names())
    missing = [model for model in targets if model._meta.db_table not in existing_tables]
    if not missing:
        return []
    with connection.schema_editor() as schema_editor:
        for model in missing:
            schema_editor.create_model(model)
    return missing
