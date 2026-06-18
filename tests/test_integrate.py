"""Tests for the integration runtime abstract bases."""

from __future__ import annotations

from django.db import models

from angee.integrate.models import Bridge, IntegrationMixin, IntegrationStatus
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
    """IntegrationMixin and Bridge are abstract inheritance bases only."""

    assert IntegrationMixin._meta.abstract is True
    assert Bridge._meta.abstract is True
    assert issubclass(Bridge, IntegrationMixin)


def test_bridge_declares_runtime_contract_methods() -> None:
    """Bridge exposes the contract domain subclasses implement."""

    for method_name in ("sync", "handle_webhook", "verify_webhook", "start_live", "stop_live"):
        assert callable(getattr(Bridge, method_name))


def test_concrete_bridge_inherits_scheduler_field() -> None:
    """A domain concrete bridge receives the scheduler index field."""

    field = ConcreteBridge._meta.get_field("next_sync_at")

    assert isinstance(field, models.DateTimeField)


def test_report_status_records_integration_telemetry() -> None:
    """report_status writes telemetry on the integration row itself."""

    integration = Integration()

    integration.report_status(status=IntegrationStatus.ERROR, error="boom")

    assert integration.status == IntegrationStatus.ERROR
    assert integration.last_used_status == "error"
    assert integration.last_error == "boom"
    assert integration.last_error_at is not None
    assert integration.last_used_at is not None

    # A bare-string status with no error clears the error timestamp.
    integration.report_status(status="active")

    assert integration.last_used_status == "active"
    assert integration.last_error == ""
    assert integration.last_error_at is None


def test_report_status_updates_unsaved_integration_in_memory() -> None:
    """report_status updates an unsaved integration without trying to persist it."""

    integration = Integration()

    integration.report_status(status=IntegrationStatus.ERROR, error="boom")

    assert integration.status == IntegrationStatus.ERROR
    assert integration.last_error == "boom"
