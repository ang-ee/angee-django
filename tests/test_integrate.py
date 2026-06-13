"""Tests for the integration runtime abstract bases."""

from __future__ import annotations

from django.db import models

from angee.integrate.models import Bridge, Capability, CapabilityStatus, IntegrationStatus
from tests.conftest import Integration


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
    """report_status writes local telemetry and calls the integration rollup when present."""

    calls: list[tuple[object, object, str]] = []
    integration = Integration()

    def note_capability_status(*, capability_key: object, status: object, error: str) -> None:
        calls.append((capability_key, status, error))

    integration.note_capability_status = note_capability_status  # type: ignore[method-assign]
    bridge = ConcreteBridge()
    bridge.integration = integration

    bridge.report_status(status=CapabilityStatus.ERROR, error="boom")

    assert bridge.status == CapabilityStatus.ERROR
    assert bridge.last_used_status == "error"
    assert bridge.last_error == "boom"
    assert bridge.last_error_at is not None
    assert bridge.last_used_at is not None
    assert calls == [("tests.concretebridge:None", CapabilityStatus.ERROR, "boom")]

    # A bare-string status with no error clears the error timestamp.
    bridge.report_status(status="active")

    assert bridge.last_used_status == "active"
    assert bridge.last_error == ""
    assert bridge.last_error_at is None


def test_report_status_updates_unsaved_integration_rollup_in_memory() -> None:
    """report_status updates an unsaved integration without trying to persist it."""

    bridge = ConcreteBridge()
    bridge.integration = Integration()

    bridge.report_status(status=CapabilityStatus.ERROR, error="boom")

    assert bridge.status == CapabilityStatus.ERROR
    assert bridge.integration.status == IntegrationStatus.ERROR
    assert bridge.integration.capability_statuses == {"tests.concretebridge:None": IntegrationStatus.ERROR.value}
    assert bridge.integration.last_error == "boom"
