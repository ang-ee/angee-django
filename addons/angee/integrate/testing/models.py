"""Concrete record-sync models shared by source-addon test suites.

The consuming suite supplies its concrete ``integrate.Integration`` and related
models before Django's test database setup.
"""

from __future__ import annotations

from angee.integrate.models import RecordLink as AbstractRecordLink
from angee.integrate.models import SyncStream as AbstractSyncStream


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
