"""Record protocol persistence contracts; transport and driver tests are separate."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.integrate.records import DiscrepancyKind, DiscrepancyStatus, LinkStatus, StreamKind, StreamPhase
from tests.conftest import make_integration
from tests.integrate_models import RecordLink, RecordRevision, SyncDiscrepancy, SyncStream
from tests.messaging_models import Channel


@pytest.fixture
def replica(record_sync_tables: None) -> Iterator[Any]:
    """A replica partition with an explicit engine actor for assertions."""

    del record_sync_tables
    with system_context(reason="test record protocol"):
        bridge = make_integration("record-protocol", model=Channel)
        yield SyncStream.objects.current(bridge, "contacts", "book", kind=StreamKind.RECORD_REPLICA)


def test_epoch_retains_links_revisions_and_quarantine(replica: Any) -> None:
    link = RecordLink.objects.observe(replica, "person:1")
    revision = RecordLink.objects.promote(
        link,
        source_payload={"name": "Ada"},
        source_hash="remote",
        mapped_payload={"name": "Ada"},
        local_hash="local",
    )
    discrepancy = SyncDiscrepancy.objects.record(
        replica,
        link=link,
        kind=DiscrepancyKind.CONFLICT,
        code="both_changed",
        source_hash="new",
    )
    successor = SyncStream.objects.bump_generation(replica)
    assert successor.generation == replica.generation + 1
    assert successor.phase == StreamPhase.BASELINE
    assert successor.cursor == {}
    assert SyncStream.objects.bump_generation(replica).pk == successor.pk
    link.refresh_from_db()
    discrepancy.refresh_from_db()
    assert link.stream_id == discrepancy.stream_id == successor.pk
    assert link.last_verified_generation == replica.generation
    assert RecordRevision.objects.get(pk=revision.pk).link_id == link.pk
    assert RecordLink.objects.observe(successor, "person:1").pk == link.pk
    link.refresh_from_db()
    assert link.last_verified_generation == successor.generation


def test_sweep_absence_unavailable_then_tombstone_and_reappearance(replica: Any) -> None:
    link = RecordLink.objects.observe(replica, "person:1")
    assert RecordLink.objects.mark_absent(replica, [link.external_key]) == 1
    link.refresh_from_db()
    assert (link.status, link.absence_count, link.tombstoned_at) == (LinkStatus.UNAVAILABLE, 1, None)
    assert RecordLink.objects.mark_absent(replica, [link.external_key]) == 1
    link.refresh_from_db()
    assert (link.status, link.absence_count) == (LinkStatus.TOMBSTONE, 2)
    retained_time = link.tombstoned_at
    assert retained_time is not None
    RecordLink.objects.tombstone(link)
    assert link.tombstoned_at == retained_time
    link = RecordLink.objects.observe(replica, link.external_key)
    assert (link.status, link.absence_count, link.tombstoned_at) == (LinkStatus.TOMBSTONE, 0, retained_time)
    RecordLink.objects.promote(link, source_payload={}, source_hash="remote", mapped_payload={}, local_hash="local")
    assert (link.status, link.tombstoned_at) == (LinkStatus.CURRENT, None)


def test_revision_numbering_and_all_mutation_paths_refuse_edits(replica: Any) -> None:
    link = RecordLink.objects.observe(replica, "person:1")
    first = RecordRevision.objects.append(link, source_payload={"v": 1}, source_hash="one", mapping_version=1)
    second = RecordRevision.objects.append(link, source_payload={"v": 2}, source_hash="two", mapping_version=1)
    assert (first.number, second.number, second.prior_id) == (1, 2, first.pk)
    first.source_hash = "edited"
    mutations = (
        lambda: first.save(),
        lambda: first.delete(),
        lambda: RecordRevision.objects.filter(pk=first.pk).update(source_hash="edited"),
        lambda: RecordRevision.objects.bulk_update([first], ["source_hash"]),
        lambda: RecordRevision.objects.filter(pk=first.pk).delete(),
        lambda: RecordRevision._base_manager.filter(pk=first.pk).update(source_hash="edited"),
    )
    for mutate in mutations:
        with pytest.raises(ValidationError, match="immutable"):
            mutate()
    first.refresh_from_db()
    assert first.source_hash == "one"


def test_page_records_and_cursor_roll_back_together(replica: Any) -> None:
    with pytest.raises(RuntimeError, match="application failed"):
        with transaction.atomic():
            RecordLink.objects.observe(replica, "person:1")
            SyncStream.objects.advance(replica, {"next": "page2"})
            raise RuntimeError("application failed")
    replica.refresh_from_db()
    assert replica.cursor == {}
    assert not RecordLink.objects.filter(stream=replica).exists()


def test_retired_epoch_rejects_late_page_before_records(replica: Any) -> None:
    SyncStream.objects.bump_generation(replica)
    with pytest.raises(RuntimeError, match="retired stream generation"):
        RecordLink.objects.observe(replica, "person:late")
    with pytest.raises(RuntimeError, match="retired stream generation"):
        SyncStream.objects.advance(replica, {"late": True})
    assert not RecordLink.objects.exists()


def test_discrepancy_coalescing_rescan_and_resolution_history(replica: Any) -> None:
    first = SyncDiscrepancy.objects.record(replica, kind=DiscrepancyKind.SEMANTIC, code="bad_name", source_hash="one")
    repeated = SyncDiscrepancy.objects.record(
        replica,
        kind=DiscrepancyKind.SEMANTIC,
        code="bad_name",
        source_hash="one",
        details={"attempt": 2},
    )
    future = SyncDiscrepancy.objects.record(
        replica,
        kind=DiscrepancyKind.MISSING_DEPENDENCY,
        code="missing",
        source_hash="two",
        retry_at=timezone.now() + timedelta(days=1),
    )
    assert repeated.pk == first.pk
    assert [row.pk for row in SyncDiscrepancy.objects.rescan(replica)] == [first.pk]
    assert SyncDiscrepancy.objects.resolve(first).status == DiscrepancyStatus.RESOLVED
    replacement = SyncDiscrepancy.objects.record(
        replica,
        kind=DiscrepancyKind.SEMANTIC,
        code="bad_name",
        source_hash="one",
    )
    assert replacement.pk not in (first.pk, future.pk)
    assert SyncDiscrepancy.objects.filter(pk=first.pk, status=DiscrepancyStatus.RESOLVED).exists()


def test_quarantine_survives_observation_and_absence_until_last_resolution(replica: Any) -> None:
    link = RecordLink.objects.observe(replica, "person:1")
    first = SyncDiscrepancy.objects.record(replica, link=link, kind=DiscrepancyKind.CONFLICT, code="conflict")
    second = SyncDiscrepancy.objects.record(replica, link=link, kind=DiscrepancyKind.SEMANTIC, code="invalid")
    RecordLink.objects.observe(replica, link.external_key, remote_version="changed")
    for _ in range(replica.absence_threshold):
        RecordLink.objects.mark_absent(replica, [link.external_key])
    link.refresh_from_db()
    assert link.status == LinkStatus.DISCREPANT
    assert link.tombstoned_at is None
    SyncDiscrepancy.objects.resolve(first)
    link.refresh_from_db()
    assert link.status == LinkStatus.DISCREPANT
    SyncDiscrepancy.objects.resolve(second)
    link.refresh_from_db()
    assert link.status == LinkStatus.OBSERVED


def test_event_feeds_reject_record_links(record_sync_tables: None) -> None:
    del record_sync_tables
    with system_context(reason="test event feed identity"):
        bridge = make_integration("event-protocol", model=Channel)
        stream = SyncStream.objects.current(bridge, "messages", "inbox")
        with pytest.raises(ValidationError, match="Event feeds"):
            RecordLink.objects.observe(stream, "message:1")
        assert not RecordLink.objects.exists()


def test_current_partition_query_returns_latest_generation_only(record_sync_tables: None) -> None:
    del record_sync_tables
    with system_context(reason="test current stream selection"):
        bridge = make_integration("partition-protocol", model=Channel)
        old = SyncStream.objects.current(bridge, "messages", "inbox", cursor={"uid": 1})
        sent = SyncStream.objects.current(bridge, "messages", "sent")
        current = SyncStream.objects.bump_generation(old)
        assert list(SyncStream.objects.current_for_bridge(bridge, "messages").values_list("pk", flat=True)) == [
            current.pk,
            sent.pk,
        ]
        assert SyncStream.objects.current(bridge, "messages", "inbox", cursor={"uid": 999}).cursor == {}
