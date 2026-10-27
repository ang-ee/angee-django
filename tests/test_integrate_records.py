"""Record protocol persistence contracts; transport and driver tests are separate."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import models, transaction
from django.utils import timezone
from rebac import actor_context, system_context

from angee.integrate.impl import BridgeImpl
from angee.integrate.states import (
    ConflictKeep,
    LinkStatus,
    StreamDirection,
    StreamKind,
    StreamPhase,
    SyncFailureKind,
    SyncFailureStatus,
)
from angee.integrate.testing.integration import Integration
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.testing.models import Channel
from angee.models import AngeeQuerySet, AngeeUnscopedQuerySet
from tests.conftest import current_stream, make_integration
from tests.mtidemo.models import MtiChild, MtiParent
from tests.stream_adapters import applied_history, failure_state


@pytest.fixture
def replica(composed_tables: None) -> Iterator[Any]:
    """A replica partition with an explicit engine actor for assertions."""

    del composed_tables
    with system_context(reason="test record protocol"):
        bridge = make_integration("record-protocol", model=Channel)
        yield current_stream(bridge, "contacts", "book", kind=StreamKind.RECORD_REPLICA)


def test_record_managers_preserve_native_locking_querysets(replica: Any) -> None:
    """Public, base and related managers keep locks through bound queryset chains."""

    link = RecordLink.objects.observe(replica, "person:locked")
    RecordLink.objects.promote(
        link,
        source_payload={},
        source_hash="remote",
        mapped_payload={},
    )
    with transaction.atomic():
        for row in (replica, link):
            model = type(row)
            assert model._default_manager is model.objects
            for manager in (model.objects, model._base_manager):
                queryset = manager.filter(pk=row.pk).order_by("pk").lock_if_supported()
                expected = AngeeQuerySet
                if manager is model._base_manager:
                    expected = AngeeUnscopedQuerySet
                assert isinstance(queryset, expected)
                assert queryset.query.select_for_update is True
                assert list(queryset) == [row]
                assert manager.locked_get(pk=row.pk) == row
                assert manager.from_public_id(str(row.sqid)) == row
        for manager, row in ((replica.links, link),):
            queryset = manager.filter(pk=row.pk).lock_if_supported()
            assert isinstance(queryset, AngeeQuerySet)
            assert list(queryset) == [row]


def test_record_link_keys_mti_target_on_canonical_parent(replica: Any) -> None:
    """The link target shares the canonical parent identity used by other edges."""

    child = MtiChild.objects.create(title="Target")
    link = RecordLink.objects.observe(replica, "mti-target", target=child)
    assert link.target_content_type.model_class() is MtiParent
    assert link.target_object_id == str(child.pk)


def test_epoch_retains_links_history_and_quarantine(replica: Any) -> None:
    link = RecordLink.objects.observe(replica, "person:1")
    revision = RecordLink.objects.promote(
        link,
        source_payload={"name": "Ada"},
        source_hash="remote",
        mapped_payload={"name": "Ada"},
    )
    failure = link.record_failure(kind=SyncFailureKind.CONFLICT, code="both_changed", source_hash="new")
    successor = SyncStream.objects.bump_generation(replica)
    assert successor.generation == replica.generation + 1
    assert successor.phase == StreamPhase.BASELINE
    assert successor.cursor == {}
    assert SyncStream.objects.bump_generation(replica).pk == successor.pk
    link.refresh_from_db()

    failure = failure_state(failure)
    assert link.stream_id == failure.stream.pk == successor.pk
    assert link.last_verified_generation == replica.generation
    assert link.history.get(history_id=revision.history_id).id == link.pk
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
def test_baseline_completion_resets_each_generation_and_reads_persisted_progress(replica: Any, exhausted: bool) -> None:
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
    assert not successor.has_completed_baseline()


def test_baseline_completion_reads_deferred_identity(replica: Any) -> None:
    deferred = SyncStream.objects.only("pk").get(pk=replica.pk)
    assert not replica.has_completed_baseline()
    other = SyncStream.objects.get(pk=replica.pk)
    SyncStream.objects.advance(other, {}, exhausted=True)
    assert deferred.has_completed_baseline()
    assert replica.has_completed_baseline()


def test_baseline_completion_is_independent_of_actor_visibility(composed_tables: None) -> None:
    del composed_tables
    call_command("rebac", "sync", verbosity=0)
    with system_context(reason="test baseline completion authorization"):
        bridge = make_integration("baseline-completion-rebac", model=Channel)
        other = get_user_model().objects.create_user(username="baseline-completion-other")
        stream = current_stream(bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
        SyncStream.objects.advance(stream, {}, exhausted=True)
        successor = SyncStream.objects.bump_generation(stream)
        deferred = SyncStream.objects.only("pk").get(pk=successor.pk)

    with actor_context(other):
        assert not SyncStream.objects.filter(integration=bridge).exists()
        assert stream.has_completed_baseline()
        assert not successor.has_completed_baseline()
        assert not deferred.has_completed_baseline()


def test_keep_local_before_first_baseline_preserves_conflict(replica: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    replica.direction = StreamDirection.BIDIRECTIONAL
    replica.save(update_fields=["direction"])
    link = RecordLink.objects.observe(replica, "person:deleted", target=replica)
    conflict = link.record_failure(kind=SyncFailureKind.CONFLICT, code="both_changed", source_hash="")
    retained_conflict = next(
        (row for row in SyncStream.objects.get(pk=replica.pk).sync_failures(open_only=False) if row.id == conflict.pk)
    )
    retained_link = RecordLink.objects.filter(pk=link.pk).values().get()
    adapter = Mock(spec=BridgeImpl, supports_identity_reads=True)
    monkeypatch.setattr(Channel, "capability_impl", property(lambda self: adapter))

    with pytest.raises(ValidationError, match="Keeping local changes requires a completed writable baseline\\."):
        conflict.resolve_conflict(keep=ConflictKeep.LOCAL)

    assert RecordLink.objects.get(pk=link.pk).failures(open_only=False) == (retained_conflict,)
    assert RecordLink.objects.filter(pk=link.pk).values().get() == retained_link
    assert not applied_history().filter(id=link.pk).exists()
    adapter.read_keys.assert_not_called()
    adapter.write_back.assert_not_called()
    adapter.close.assert_called_once_with()


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
    RecordLink.objects.promote(
        link,
        source_payload={},
        source_hash="remote",
        mapped_payload={},
    )
    assert (link.status, link.tombstoned_at) == (LinkStatus.CURRENT, None)


@pytest.mark.parametrize("operation", ["observe", "promote"])
def test_explicit_target_withdrawal_differs_from_omitted_target(replica: Any, operation: str) -> None:
    link = RecordLink.objects.observe(replica, "person:1", target=replica)
    evidence = dict(
        source_payload={},
        source_hash="remote",
        mapped_payload={},
    )
    if operation == "observe":
        RecordLink.objects.observe(replica, link.external_key)
    else:
        RecordLink.objects.promote(link, **evidence)
    link.refresh_from_db()
    assert link.target_object_id == str(replica.pk)
    if operation == "observe":
        RecordLink.objects.observe(replica, link.external_key, target=None)
    else:
        RecordLink.objects.promote(link, target=None, **evidence)
    link.refresh_from_db()
    assert (link.target_content_type_id, link.target_object_id, link.status) == (None, None, LinkStatus.WITHDRAWN)


def test_release_target_keeps_the_identity_current_until_its_row_is_rebuilt(replica: Any) -> None:
    link = RecordLink.objects.observe(replica, "person:1", target=replica)
    RecordLink.objects.promote(link, source_payload={}, source_hash="remote", mapped_payload={})
    released = RecordLink.objects.release_target(link)
    link.refresh_from_db()
    assert (released.pk, link.target_content_type_id, link.target_object_id) == (link.pk, None, None)
    assert link.status == LinkStatus.CURRENT
    RecordLink.objects.observe(replica, link.external_key, target=replica)
    link.refresh_from_db()
    assert (link.target_object_id, link.status) == (str(replica.pk), LinkStatus.CURRENT)


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
    stream = current_stream(bridge, "other", kind=StreamKind.RECORD_REPLICA)
    foreign = RecordLink.objects.observe(stream, "aggregate:foreign")
    with pytest.raises(ValidationError, match="same stream"):
        RecordLink.objects.observe(replica, "member:foreign", parent=foreign)


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


def test_failure_coalescing_rescan_and_resolution_history(replica: Any) -> None:
    link = RecordLink.objects.observe(replica, "person:1")
    first = link.record_failure(kind=SyncFailureKind.SEMANTIC, code="bad_name", source_hash="one")
    repeated = link.record_failure(
        kind=SyncFailureKind.SEMANTIC, code="bad_name", source_hash="one", details={"attempt": 2}
    )
    future = replica.record_failure(kind=SyncFailureKind.MISSING_DEPENDENCY, code="missing", source_hash="two")
    assert repeated.pk == first.pk
    first = failure_state(first, retry_at=timezone.now())
    assert [row.pk for row in RecordLink.objects.filter(stream=replica).rescan(limit=100)] == [first.pk]
    assert first.resolve().status == SyncFailureStatus.RESOLVED
    replacement = replica.record_failure(kind=SyncFailureKind.SEMANTIC, code="bad_name", source_hash="one")
    assert replacement.pk not in (first.pk, future.pk)
    assert any(
        (
            True
            for row in SyncStream.objects.get(pk=replica.pk).sync_failures(open_only=False)
            if row.id == first.pk
            if row.status == SyncFailureStatus.RESOLVED
        )
    )


def test_retry_is_due_now_but_preserves_conflict_and_resolved_history_guards(replica: Any) -> None:
    """Retry advances scheduling only; conflict admission and immutable history survive."""

    link = RecordLink.objects.observe(replica, "person:retry")
    failure = link.record_failure(kind=SyncFailureKind.SEMANTIC, code="invalid")
    before = timezone.now()
    retried = failure.retry()
    assert retried.status == SyncFailureStatus.RETRY and retried.retry_at >= before
    assert [row.pk for row in RecordLink.objects.filter(stream=replica).rescan(limit=100)] == [retried.pk]
    conflict = link.record_failure(kind=SyncFailureKind.CONFLICT, code="both_changed")
    conflict.retry()
    assert RecordLink.objects.filter(stream=replica).rescan(limit=100) == ()
    resolved = failure.resolve()
    with pytest.raises(ValidationError, match="resolved failure"):
        resolved.retry()


@pytest.mark.parametrize("changed", [{"source_hash": "changed"}, {"mapping_version": 2}])
def test_new_evidence_replaces_same_link_problem_without_losing_history_or_conflicts(replica: Any, changed) -> None:
    link = RecordLink.objects.observe(replica, "person:changed")
    original = link.record_failure(kind=SyncFailureKind.SEMANTIC, code="invalid")
    unrelated = link.record_failure(kind=SyncFailureKind.MISSING_DEPENDENCY, code="parent")
    conflict = link.record_failure(kind=SyncFailureKind.CONFLICT, code="both_changed")
    replacement = link.record_failure(kind=SyncFailureKind.SEMANTIC, code="invalid", **changed)
    assert {row.pk for row in link.failures(open_only=True)} == {unrelated.pk, conflict.pk, replacement.pk}
    assert next(row for row in link.failures() if row.pk == original.pk).status == SyncFailureStatus.RESOLVED
    assert link.record_failure(kind=SyncFailureKind.SEMANTIC, code="invalid", **changed).pk == replacement.pk
    newer_conflict = link.record_failure(kind=SyncFailureKind.CONFLICT, code="both_changed", **changed)
    assert {row.pk for row in link.failures(open_only=True)} >= {conflict.pk, newer_conflict.pk}

    first_event = replica.record_failure(kind=SyncFailureKind.SEMANTIC, code="invalid")
    second_event = replica.record_failure(kind=SyncFailureKind.SEMANTIC, code="invalid", **changed)
    stream_failures = {row.pk for row in replica.failures(open_only=True)}
    assert {first_event.pk, second_event.pk} <= stream_failures


def test_failure_rescan_is_bounded_before_materialization(replica: Any) -> None:
    rows = [
        RecordLink.objects.observe(replica, f"person:{index}").record_failure(
            kind=SyncFailureKind.SEMANTIC, code=f"bad_{index}"
        )
        for index in range(3)
    ]
    for row in rows:
        failure_state(row, retry_at=timezone.now())
    assert [row.pk for row in RecordLink.objects.filter(stream=replica).rescan(limit=2)] == [row.pk for row in rows[:2]]
    with pytest.raises(ValueError, match="positive"):
        RecordLink.objects.filter(stream=replica).rescan(limit=0)


def test_rescan_filters_conflicted_aggregates_and_unlinked_failures_before_limit(replica: Any) -> None:
    parent = RecordLink.objects.observe(replica, "aggregate:conflicted")
    child = RecordLink.objects.observe(replica, "member:conflicted", parent=parent)
    for kind, link, code in (
        (SyncFailureKind.CONFLICT, child, "conflict"),
        (SyncFailureKind.SEMANTIC, parent, "blocked_parent"),
        (SyncFailureKind.SEMANTIC, child, "blocked_child"),
        (SyncFailureKind.SEMANTIC, None, "no_identity"),
    ):
        (link or replica).record_failure(kind=kind, code=code)
    retryable = RecordLink.objects.observe(replica, "person:retryable").record_failure(
        kind=SyncFailureKind.SEMANTIC, code="retryable"
    )
    retryable = failure_state(retryable, retry_at=timezone.now())
    assert [row.pk for row in RecordLink.objects.filter(stream=replica).rescan(limit=1)] == [retryable.pk]


def test_quarantine_survives_observation_and_absence_until_last_resolution(replica: Any) -> None:
    link = RecordLink.objects.observe(replica, "person:1")
    first = link.record_failure(kind=SyncFailureKind.MISSING_DEPENDENCY, code="missing")
    second = link.record_failure(kind=SyncFailureKind.SEMANTIC, code="invalid")
    RecordLink.objects.observe(replica, link.external_key, remote_version="changed")
    for _ in range(replica.absence_threshold):
        RecordLink.objects.mark_absent(replica, [link.external_key])
    link.refresh_from_db()
    assert link.status == LinkStatus.FAILED
    assert link.tombstoned_at is None
    first.resolve()
    link.refresh_from_db()
    assert link.status == LinkStatus.FAILED
    second.resolve()
    link.refresh_from_db()
    assert link.status == LinkStatus.OBSERVED


def test_event_feeds_reject_record_links(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test event feed identity"):
        bridge = make_integration("event-protocol", model=Channel)
        stream = current_stream(bridge, "messages", "inbox")
        with pytest.raises(ValidationError, match="Event feeds"):
            RecordLink.objects.observe(stream, "message:1")
        assert not RecordLink.objects.exists()


def test_current_partition_query_returns_latest_generation_only(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test current stream selection"):
        bridge = make_integration("partition-protocol", model=Channel)
        old = current_stream(bridge, "messages", "inbox", cursor={"uid": 1})
        sent = current_stream(bridge, "messages", "sent")
        current = SyncStream.objects.bump_generation(old)
        assert list(SyncStream.objects.current_for_bridge(bridge, "messages").values_list("pk", flat=True)) == [
            current.pk,
            sent.pk,
        ]
        assert current_stream(bridge, "messages", "inbox", cursor={"uid": 999}).cursor == {}
        contacts = current_stream(bridge, "contacts")
        other = current_stream(make_integration("partition-other", model=Channel), "messages", "inbox")
        every_key = list(SyncStream.objects.current_for_bridge(bridge).values_list("pk", flat=True))
        assert every_key == [contacts.pk, current.pk, sent.pk]
        assert other.pk not in every_key


def test_stream_uses_a_protected_integration_foreign_key(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test stream bridge identity"):
        bridge = make_integration("stream-identity", model=Channel)
        stream = current_stream(bridge, "messages")
        stream.refresh_from_db()
        assert stream.integration_id == bridge.pk
        assert stream.integration.concrete_capability() == bridge
        field = stream._meta.get_field("integration")
        assert isinstance(field, models.ForeignKey)
        assert field.remote_field.on_delete is models.PROTECT
        assert {"bridge_id", "bridge_ct_id"}.isdisjoint(field.column for field in stream._meta.concrete_fields)
        successor = SyncStream.objects.bump_generation(stream)
        assert successor.integration_id == bridge.pk
        assert SyncStream.objects.filter(integration=bridge.pk).count() == 2
        with pytest.raises(models.ProtectedError):
            bridge.delete()


def test_stream_rejects_an_integration_without_a_concrete_bridge(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test stream requires a bridge"):
        integration = make_integration("stream-no-bridge")
        with pytest.raises(ValidationError, match="concrete Integration child"):
            current_stream(integration, "messages")
        assert not SyncStream.objects.exists()


def test_stream_and_links_follow_the_integration_foreign_key_owner(composed_tables: None) -> None:
    del composed_tables
    call_command("rebac", "sync", verbosity=0)
    with system_context(reason="test derived stream authorization"):
        bridge = make_integration("stream-rebac", model=Channel)
        owner = get_user_model().objects.get(pk=bridge.owner_id)
        other = get_user_model().objects.create_user(username="stream-rebac-other")
        stream = current_stream(bridge, "contacts", kind=StreamKind.RECORD_REPLICA)
        link = RecordLink.objects.observe(stream, "person:1")

    for model, row in ((SyncStream, stream), (RecordLink, link)):
        assert model.objects.with_actor(owner).filter(pk=row.pk).exists()
        assert not model.objects.with_actor(other).filter(pk=row.pk).exists()

    with system_context(reason="test derived stream owner change"):
        Integration.objects.filter(pk=bridge.pk).update(owner=other)

    for model, row in ((SyncStream, stream), (RecordLink, link)):
        assert not model.objects.with_actor(owner).filter(pk=row.pk).exists()
        assert model.objects.with_actor(other).filter(pk=row.pk).exists()
