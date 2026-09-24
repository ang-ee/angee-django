"""Record protocol persistence contracts; transport and driver tests are separate."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connections, models, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import actor_context, system_context

from angee.base.models import AngeeQuerySet, AngeeUnscopedQuerySet
from angee.integrate.impl import BridgeImpl
from angee.integrate.states import (
    ConflictKeep,
    DiscrepancyKind,
    DiscrepancyStatus,
    LinkStatus,
    StreamDirection,
    StreamKind,
    StreamPhase,
)
from tests.conftest import make_integration
from tests.integrate_models import Integration, RecordLink, RecordRevision, SyncDiscrepancy, SyncStream
from tests.messaging_models import Channel


@pytest.fixture
def replica(record_sync_tables: None) -> Iterator[Any]:
    """A replica partition with an explicit engine actor for assertions."""

    del record_sync_tables
    with system_context(reason="test record protocol"):
        bridge = make_integration("record-protocol", model=Channel)
        yield SyncStream.objects.current(bridge, "contacts", "book", kind=StreamKind.RECORD_REPLICA)


def test_record_managers_preserve_native_locking_querysets(replica: Any) -> None:
    """Public, base and related managers keep locks through bound queryset chains."""

    link = RecordLink.objects.observe(replica, "person:locked")
    revision = RecordLink.objects.promote(
        link, source_payload={}, source_hash="remote", mapped_payload={}, local_hash="local"
    )
    discrepancy = SyncDiscrepancy.objects.record(
        replica, link=link, kind=DiscrepancyKind.SEMANTIC, code="lock-regression"
    )
    using = replica._state.db
    features = connections[using].features
    with transaction.atomic(using=using):
        for row in (replica, link, revision, discrepancy):
            model = type(row)
            assert model._default_manager is model.objects
            for manager in (model.objects, model._base_manager):
                queryset = manager.db_manager(using).filter(pk=row.pk).order_by("pk").lock_if_supported()
                expected = (
                    AngeeUnscopedQuerySet
                    if manager is model._base_manager and model is not RecordRevision
                    else AngeeQuerySet
                )
                assert isinstance(queryset, expected)
                assert queryset.db == using
                assert queryset.query.select_for_update is features.has_select_for_update
                assert list(queryset) == [row]
        for manager, row in ((replica.links, link), (replica.discrepancies, discrepancy), (link.revisions, revision)):
            queryset = manager.db_manager(using).filter(pk=row.pk).lock_if_supported()
            assert isinstance(queryset, AngeeQuerySet)
            assert list(queryset) == [row]


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


def test_resync_request_follows_latest_epoch_and_leaves_bump_to_driver(replica: Any) -> None:
    """A retained historical row requests work on its successor without another epoch."""

    successor = SyncStream.objects.bump_generation(replica)
    requested = SyncStream.objects.request_resync(replica)
    assert requested.pk == successor.pk
    assert requested.resync_required
    assert SyncStream.objects.count() == 2
    assert SyncStream.objects.request_resync(replica).pk == successor.pk
    fresh = SyncStream.objects.bump_generation(requested)
    assert fresh.generation == successor.generation + 1
    assert fresh.phase == StreamPhase.BASELINE and not fresh.resync_required


@pytest.mark.parametrize("exhausted", [False, True])
def test_baseline_completion_survives_epochs_and_reads_persisted_progress(replica: Any, exhausted: bool) -> None:
    # Another partition's completed baseline never enables this one's pushes.
    other = SyncStream.objects.create(
        integration_id=replica.integration_id,
        key=replica.key,
        partition="other-book",
        kind=replica.kind,
        direction=replica.direction,
        phase=StreamPhase.DELTA,
    )
    assert other.has_completed_baseline()
    assert not replica.has_completed_baseline()
    fresh = SyncStream.objects.get(pk=replica.pk)
    SyncStream.objects.advance(fresh, {"page": 1}, exhausted=exhausted)
    assert replica.phase == StreamPhase.BASELINE
    assert replica.has_completed_baseline() is exhausted
    successor = SyncStream.objects.bump_generation(replica)
    assert successor.phase == StreamPhase.BASELINE
    assert successor.has_completed_baseline() is exhausted


def test_baseline_completion_reads_deferred_identity_on_operation_alias(replica: Any, database_alias: Any) -> None:
    deferred = SyncStream.objects.only("pk").get(pk=replica.pk)

    def reject_default_query(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Baseline completion must read only the operation alias.")

    with database_alias("baseline_completion") as alias:
        other = SyncStream.objects.using(alias).get(pk=replica.pk)
        SyncStream.objects.db_manager(alias).advance(other, {}, exhausted=True, using=alias)
        with connections["default"].execute_wrapper(reject_default_query):
            assert deferred.has_completed_baseline(using=alias)
    assert not replica.has_completed_baseline()


def test_baseline_completion_is_independent_of_actor_visibility(record_sync_tables: None) -> None:
    del record_sync_tables
    call_command("rebac", "sync", verbosity=0)
    with system_context(reason="test baseline completion authorization"):
        bridge = make_integration("baseline-completion-rebac", model=Channel)
        other = get_user_model().objects.create_user(username="baseline-completion-other")
        stream = SyncStream.objects.current(bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
        SyncStream.objects.advance(stream, {}, exhausted=True)
        successor = SyncStream.objects.bump_generation(stream)
        deferred = SyncStream.objects.only("pk").get(pk=successor.pk)

    with actor_context(other):
        assert not SyncStream.objects.filter(integration=bridge).exists()
        assert stream.has_completed_baseline()
        assert successor.has_completed_baseline()
        assert deferred.has_completed_baseline()


def test_keep_local_before_first_baseline_preserves_conflict(replica: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    replica.direction = StreamDirection.BIDIRECTIONAL
    replica.save(update_fields=["direction"])
    link = RecordLink.objects.observe(replica, "person:deleted", target=replica)
    conflict = SyncDiscrepancy.objects.record(
        replica, link=link, kind=DiscrepancyKind.CONFLICT, code="both_changed", source_hash=""
    )
    retained_conflict = SyncDiscrepancy.objects.filter(pk=conflict.pk).values().get()
    retained_link = RecordLink.objects.filter(pk=link.pk).values().get()
    adapter = Mock(spec=BridgeImpl, supports_identity_reads=True)
    monkeypatch.setattr(Channel, "backend", property(lambda self: adapter))

    with pytest.raises(ValidationError, match="Complete the stream baseline before resolving its conflict\\."):
        SyncDiscrepancy.objects.resolve_conflict(conflict, keep=ConflictKeep.LOCAL)

    assert list(SyncDiscrepancy.objects.filter(link=link).values()) == [retained_conflict]
    assert RecordLink.objects.filter(pk=link.pk).values().get() == retained_link
    assert not RecordRevision.objects.filter(link=link).exists()
    adapter.read_keys.assert_not_called()
    adapter.write_back.assert_not_called()
    adapter.close.assert_called_once_with()


@pytest.mark.parametrize("count", [1, 8])
def test_record_link_sync_evidence_uses_bounded_queries_on_operation_alias(
    replica: Any, database_alias: Any, count: int
) -> None:
    expected: dict[str, tuple[list[int], list[int]]] = {}
    for index in range(count):
        link = RecordLink.objects.observe(replica, f"person:{index}")
        revisions = []
        if index % 3 != 2:
            for number in (1, 2):
                revision = RecordRevision.objects.append(
                    link, source_payload={"version": number}, source_hash=str(number), mapping_version=number
                )
            revisions = [revision.pk]
        conflicts = []
        for status in DiscrepancyStatus.values:
            discrepancy = SyncDiscrepancy.objects.create(
                stream=replica,
                link=link,
                kind=DiscrepancyKind.CONFLICT,
                code=f"both_changed-{index}-{status}",
                status=status,
            )
            if status != DiscrepancyStatus.RESOLVED:
                conflicts.append(discrepancy.pk)
        SyncDiscrepancy.objects.record(replica, link=link, kind=DiscrepancyKind.SEMANTIC, code=f"semantic-{index}")
        expected[link.external_key] = (revisions, conflicts)
    RecordLink.objects.observe(replica, "outside-page")

    def reject_default_query(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Page comparison evidence must read only the operation alias.")

    with database_alias("sync_evidence") as alias:
        # Materialize the isolated database before refusing default reads.
        SyncStream.objects.using(alias).get(pk=replica.pk)
        with (
            connections["default"].execute_wrapper(reject_default_query),
            CaptureQueriesContext(connections[alias]) as queries,
        ):
            links = list(
                RecordLink.objects.filter(stream=replica, external_key__in=[*expected, "missing"])
                .with_sync_evidence(using=alias)
                .order_by("pk")
            )
            actual = {
                link.external_key: (
                    [revision.pk for revision in link.latest_revisions],
                    [conflict.pk for conflict in link.open_conflicts],
                )
                for link in links
            }
        assert len(queries) == 3
    assert actual == expected


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


@pytest.mark.parametrize("operation", ["observe", "promote"])
def test_explicit_target_withdrawal_differs_from_omitted_target(replica: Any, operation: str) -> None:
    link = RecordLink.objects.observe(replica, "person:1", target=replica)
    evidence = dict(source_payload={}, source_hash="remote", mapped_payload={}, local_hash="local")
    if operation == "observe":
        RecordLink.objects.observe(replica, link.external_key)
    else:
        RecordLink.objects.promote(link, **evidence)
    link.refresh_from_db()
    assert link.target_id == str(replica.pk)
    if operation == "observe":
        RecordLink.objects.observe(replica, link.external_key, target=None)
    else:
        RecordLink.objects.promote(link, target=None, **evidence)
    link.refresh_from_db()
    assert (link.target_ct_id, link.target_id, link.status) == (None, None, LinkStatus.WITHDRAWN)


def test_child_absence_requires_parent_absence_evidence(replica: Any) -> None:
    parent = RecordLink.objects.observe(replica, "aggregate:1")
    child = RecordLink.objects.observe(replica, "member:1", parent=parent)
    assert RecordLink.objects.mark_absent(replica, [child.external_key]) == 0
    child.refresh_from_db()
    assert child.absence_count == 0
    assert RecordLink.objects.mark_absent(replica, [parent.external_key]) == 1
    assert RecordLink.objects.mark_absent(replica, [child.external_key]) == 1
    child.refresh_from_db()
    assert (child.status, child.absence_count) == (LinkStatus.UNAVAILABLE, 1)
    RecordLink.objects.observe(replica, parent.external_key)
    assert RecordLink.objects.mark_absent(replica, [child.external_key]) == 0


def test_record_parent_is_one_root_in_same_stream_and_cannot_be_reassigned(replica: Any) -> None:
    parent = RecordLink.objects.observe(replica, "aggregate:1")
    other = RecordLink.objects.observe(replica, "aggregate:2")
    child = RecordLink.objects.observe(replica, "member:1", parent=parent)
    assert RecordLink.objects.observe(replica, child.external_key).parent_id == parent.pk
    for replacement in (None, other):
        with pytest.raises(ValidationError, match="immutable"):
            RecordLink.objects.observe(replica, child.external_key, parent=replacement)
    with pytest.raises(ValidationError, match="root link"):
        RecordLink.objects.observe(replica, "nested:1", parent=child)
    with pytest.raises(ValidationError, match="root link"):
        RecordLink.objects.observe(replica, parent.external_key, parent=parent)
    with pytest.raises(ValidationError, match="root with children"):
        RecordLink.objects.observe(replica, parent.external_key, parent=other)
    bridge = make_integration("other-record-protocol", model=Channel)
    stream = SyncStream.objects.current(bridge, "other", kind=StreamKind.RECORD_REPLICA)
    foreign = RecordLink.objects.observe(stream, "aggregate:foreign")
    with pytest.raises(ValidationError, match="same stream"):
        RecordLink.objects.observe(replica, "member:foreign", parent=foreign)


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
    link = RecordLink.objects.observe(replica, "person:1")
    first = SyncDiscrepancy.objects.record(
        replica, link=link, kind=DiscrepancyKind.SEMANTIC, code="bad_name", source_hash="one"
    )
    repeated = SyncDiscrepancy.objects.record(
        replica,
        kind=DiscrepancyKind.SEMANTIC,
        code="bad_name",
        source_hash="one",
        details={"attempt": 2},
        link=link,
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


@pytest.mark.parametrize("status", [DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY])
def test_open_discrepancy_unique_index_preserves_resolved_history(replica: Any, status: str) -> None:
    """The database enforces uniqueness through the generated openness column."""

    using = replica._state.db
    identity = {
        "stream": replica,
        "kind": DiscrepancyKind.SEMANTIC,
        "code": "invalid",
        "source_hash": "same-version",
        "mapping_version": 1,
    }
    first = SyncDiscrepancy.objects.db_manager(using).record(**identity)
    if status == DiscrepancyStatus.RETRY:
        first = SyncDiscrepancy.objects.db_manager(using).retry(first)
    first.refresh_from_db(using=using)
    assert first.is_open

    # Bypass manager coalescing so this exercises the native partial index.
    with pytest.raises(IntegrityError), transaction.atomic(using=using):
        SyncDiscrepancy.objects.db_manager(using).create(**identity, status=status)

    resolved = SyncDiscrepancy.objects.db_manager(using).resolve(first)
    assert not resolved.is_open
    replacement = SyncDiscrepancy.objects.db_manager(using).create(**identity, status=status)
    replacement.refresh_from_db(using=using)
    assert replacement.is_open
    assert set(SyncDiscrepancy.objects.db_manager(using).values_list("pk", "is_open")) == {
        (resolved.pk, False),
        (replacement.pk, True),
    }


def test_retry_is_due_now_but_preserves_conflict_and_resolved_history_guards(replica: Any) -> None:
    """Retry advances scheduling only; conflict admission and immutable history survive."""

    link = RecordLink.objects.observe(replica, "person:retry")
    discrepancy = SyncDiscrepancy.objects.record(
        replica,
        link=link,
        kind=DiscrepancyKind.SEMANTIC,
        code="invalid",
        retry_at=timezone.now() + timedelta(days=1),
    )
    before = timezone.now()
    retried = SyncDiscrepancy.objects.retry(discrepancy)
    assert retried.status == DiscrepancyStatus.RETRY and retried.retry_at >= before
    assert [row.pk for row in SyncDiscrepancy.objects.rescan(replica)] == [retried.pk]
    conflict = SyncDiscrepancy.objects.record(replica, link=link, kind=DiscrepancyKind.CONFLICT, code="both_changed")
    SyncDiscrepancy.objects.retry(conflict)
    assert SyncDiscrepancy.objects.rescan(replica) == ()
    resolved = SyncDiscrepancy.objects.resolve(discrepancy)
    with pytest.raises(ValidationError, match="resolved discrepancy"):
        SyncDiscrepancy.objects.retry(resolved)


def test_discrepancy_rescan_is_bounded_before_materialization(replica: Any) -> None:
    rows = [
        SyncDiscrepancy.objects.record(
            replica,
            link=RecordLink.objects.observe(replica, f"person:{index}"),
            kind=DiscrepancyKind.SEMANTIC,
            code=f"bad_{index}",
        )
        for index in range(3)
    ]
    assert [row.pk for row in SyncDiscrepancy.objects.rescan(replica, limit=2)] == [row.pk for row in rows[:2]]
    with pytest.raises(ValueError, match="positive"):
        SyncDiscrepancy.objects.rescan(replica, limit=0)


def test_rescan_filters_conflicted_aggregates_and_unlinked_failures_before_limit(replica: Any) -> None:
    parent = RecordLink.objects.observe(replica, "aggregate:conflicted")
    child = RecordLink.objects.observe(replica, "member:conflicted", parent=parent)
    for kind, link, code in (
        (DiscrepancyKind.CONFLICT, child, "conflict"),
        (DiscrepancyKind.SEMANTIC, parent, "blocked_parent"),
        (DiscrepancyKind.SEMANTIC, child, "blocked_child"),
        (DiscrepancyKind.SEMANTIC, None, "no_identity"),
    ):
        SyncDiscrepancy.objects.record(replica, link=link, kind=kind, code=code)
    retryable = SyncDiscrepancy.objects.record(
        replica,
        link=RecordLink.objects.observe(replica, "person:retryable"),
        kind=DiscrepancyKind.SEMANTIC,
        code="retryable",
    )
    assert [row.pk for row in SyncDiscrepancy.objects.rescan(replica, limit=1)] == [retryable.pk]


def test_quarantine_survives_observation_and_absence_until_last_resolution(replica: Any) -> None:
    link = RecordLink.objects.observe(replica, "person:1")
    first = SyncDiscrepancy.objects.record(replica, link=link, kind=DiscrepancyKind.MISSING_DEPENDENCY, code="missing")
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


def test_stream_uses_a_protected_integration_foreign_key(record_sync_tables: None) -> None:
    del record_sync_tables
    with system_context(reason="test stream bridge identity"):
        bridge = make_integration("stream-identity", model=Channel)
        stream = SyncStream.objects.current(bridge, "messages", using="default")
        stream.refresh_from_db(using="default")
        assert stream.integration_id == bridge.pk
        assert stream.integration.concrete_capability() == bridge
        field = stream._meta.get_field("integration")
        assert isinstance(field, models.ForeignKey)
        assert field.remote_field.on_delete is models.PROTECT
        assert {"bridge_id", "bridge_ct_id"}.isdisjoint(field.column for field in stream._meta.concrete_fields)
        successor = SyncStream.objects.bump_generation(stream, using="default")
        assert successor.integration_id == bridge.pk
        assert SyncStream.objects.filter(integration=bridge.pk).count() == 2
        with pytest.raises(models.ProtectedError):
            bridge.delete()


def test_stream_rejects_an_integration_without_a_concrete_bridge(record_sync_tables: None) -> None:
    del record_sync_tables
    with system_context(reason="test stream requires a bridge"):
        integration = make_integration("stream-no-bridge")
        with pytest.raises(ValidationError, match="concrete Integration child"):
            SyncStream.objects.current(integration, "messages", using="default")
        assert not SyncStream.objects.exists()


def test_stream_and_links_follow_the_integration_foreign_key_owner(record_sync_tables: None) -> None:
    del record_sync_tables
    call_command("rebac", "sync", verbosity=0)
    with system_context(reason="test derived stream authorization"):
        bridge = make_integration("stream-rebac", model=Channel)
        owner = get_user_model().objects.get(pk=bridge.owner_id)
        other = get_user_model().objects.create_user(username="stream-rebac-other")
        stream = SyncStream.objects.current(bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
        link = RecordLink.objects.observe(stream, "person:1")

    for model, row in ((SyncStream, stream), (RecordLink, link)):
        assert model.objects.with_actor(owner).filter(pk=row.pk).exists()
        assert not model.objects.with_actor(other).filter(pk=row.pk).exists()

    with system_context(reason="test derived stream owner change"):
        Integration.objects.filter(pk=bridge.pk).update(owner=other)

    for model, row in ((SyncStream, stream), (RecordLink, link)):
        assert not model.objects.with_actor(owner).filter(pk=row.pk).exists()
        assert model.objects.with_actor(other).filter(pk=row.pk).exists()
