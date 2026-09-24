"""Canonical concrete Integration shared by bare-Django test fixtures."""

from angee.integrate.models import Integration as AbstractIntegration
from angee.integrate.models import RecordLink as AbstractRecordLink
from angee.integrate.models import RecordRevision as AbstractRecordRevision
from angee.integrate.models import SyncDiscrepancy as AbstractSyncDiscrepancy
from angee.integrate.models import SyncStream as AbstractSyncStream
from angee.projects.models import IntegrationProjects


class Integration(IntegrationProjects, AbstractIntegration):
    """Concrete integration used by source-addon tests."""

    class Meta(AbstractIntegration.Meta):
        """Django model options for the canonical test integration."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_integration"
        rebac_resource_type = "integrate/integration"


class SyncStream(AbstractSyncStream):
    """Concrete stream shared by sync and adapter tests."""

    class Meta(AbstractSyncStream.Meta):
        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_sync_stream"
        rebac_resource_type = "integrate/sync_stream"


class RecordLink(AbstractRecordLink):
    """Concrete replica identity shared by sync tests."""

    class Meta(AbstractRecordLink.Meta):
        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_record_link"
        rebac_resource_type = "integrate/record_link"


class RecordRevision(AbstractRecordRevision):
    """Concrete immutable revision shared by sync tests."""

    class Meta(AbstractRecordRevision.Meta):
        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_record_revision"
        rebac_resource_type = "integrate/record_revision"


class SyncDiscrepancy(AbstractSyncDiscrepancy):
    """Concrete quarantine shared by sync tests."""

    class Meta(AbstractSyncDiscrepancy.Meta):
        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_sync_discrepancy"
        rebac_resource_type = "integrate/sync_discrepancy"


RECORD_SYNC_TEST_MODELS = (SyncStream, RecordLink, RecordRevision, SyncDiscrepancy)
"""Concrete stream state models, in foreign-key creation order."""
