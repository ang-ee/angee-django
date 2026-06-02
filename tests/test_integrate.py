"""Tests for the integration runtime abstract bases."""

from __future__ import annotations

from django.db import models

from angee.iam.models import AccountStatus
from angee.integrate.models import Bridge, Capability, CapabilityStatus
from tests.conftest import ExternalAccount


class ConcreteBridge(Bridge):
    """Concrete bridge used only to inspect inherited field declarations."""

    class Meta(Bridge.Meta):
        """Django model options for the concrete bridge test double."""

        abstract = False
        app_label = "tests"
        db_table = "test_integrate_bridge"
        rebac_resource_type = "tests/bridge"
        rebac_id_attr = "sqid"


def test_integrate_bases_are_abstract() -> None:
    """Capability and Bridge are abstract inheritance bases only."""

    assert Capability._meta.abstract is True
    assert Bridge._meta.abstract is True
    assert issubclass(Bridge, Capability)


def test_bridge_declares_runtime_contract_methods() -> None:
    """Bridge exposes the contract domain subclasses implement."""

    for method_name in ("sync", "handle_webhook", "verify_webhook", "start_live", "stop_live"):
        assert callable(getattr(Bridge, method_name))


def test_concrete_bridge_inherits_scheduler_field() -> None:
    """A domain concrete bridge receives the scheduler index field."""

    field = ConcreteBridge._meta.get_field("next_sync_at")

    assert isinstance(field, models.DateTimeField)


def test_report_status_records_telemetry_and_pushes_rollup() -> None:
    """report_status writes local telemetry and calls the account rollup when present."""

    calls: list[tuple[object, object, str]] = []
    account = ExternalAccount()

    def note_capability_status(*, capability_key: object, status: object, error: str) -> None:
        calls.append((capability_key, status, error))

    account.note_capability_status = note_capability_status  # type: ignore[method-assign]
    bridge = ConcreteBridge()
    bridge.account = account

    bridge.report_status(status=CapabilityStatus.ERROR, error="boom")

    assert bridge.status == CapabilityStatus.ERROR
    assert bridge.last_used_status == "error"
    assert bridge.last_error == "boom"
    assert bridge.last_error_at is not None
    assert bridge.last_used_at is not None
    assert calls == [("None", CapabilityStatus.ERROR, "boom")]

    # A bare-string status with no error clears the error timestamp.
    bridge.report_status(status="active")

    assert bridge.last_used_status == "active"
    assert bridge.last_error == ""
    assert bridge.last_error_at is None


def test_report_status_updates_unsaved_account_rollup_in_memory() -> None:
    """report_status updates an unsaved account without trying to persist it."""

    bridge = ConcreteBridge()
    bridge.account = ExternalAccount()

    bridge.report_status(status=CapabilityStatus.ERROR, error="boom")

    assert bridge.status == CapabilityStatus.ERROR
    assert bridge.account.status == AccountStatus.ERROR
    assert bridge.account.capability_statuses == {"None": AccountStatus.ERROR.value}
    assert bridge.account.last_error == "boom"
