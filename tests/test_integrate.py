"""Tests for the integration runtime abstract bases."""

from __future__ import annotations

from django.db import models

from angee.integrate.models import Bridge, IntegrationLifecycle, IntegrationRuntimeStatus, integration_status_axes
from angee.integrate.registry import bridge_models, check_source_kind_contracts, source_kind_models
from tests.conftest import Integration, Source, Template


class ConcreteBridge(Integration, Bridge):
    """Concrete bridge used only to inspect inherited field declarations."""

    class Meta(Bridge.Meta):
        """Django model options for the concrete bridge test double."""

        abstract = False
        app_label = "tests"
        db_table = "test_integrate_bridge"
        rebac_resource_type = "tests/bridge"
        rebac_id_attr = "sqid"


def test_integrate_bases_are_abstract() -> None:
    """Bridge is an abstract inheritance base only."""

    assert Bridge._meta.abstract is True
    assert "integration" not in {field.name for field in Bridge._meta.local_fields}


def test_bridge_declares_runtime_contract_methods() -> None:
    """Bridge exposes the contract domain subclasses implement."""

    for method_name in ("sync", "handle_webhook", "verify_webhook", "start_live", "stop_live"):
        assert callable(getattr(Bridge, method_name))


def test_concrete_bridge_inherits_scheduler_field() -> None:
    """A domain concrete bridge receives the scheduler index field."""

    field = ConcreteBridge._meta.get_field("next_sync_at")

    assert isinstance(field, models.DateTimeField)


def test_concrete_bridge_uses_django_mti_parent_link() -> None:
    """A concrete bridge is a Django MTI child of Integration."""

    parent_link = ConcreteBridge._meta.get_field("integration_ptr")

    assert parent_link.primary_key is True
    assert parent_link.remote_field.model is Integration


def test_bridge_registry_is_explicit_about_the_bridge_base() -> None:
    """Bridge discovery takes the base model from the caller that owns it."""

    assert bridge_models(Bridge)
    assert all(issubclass(model, Bridge) for model in bridge_models(Bridge))


def test_source_kind_registry_is_deterministic_and_checked() -> None:
    """Source-kind output declarations are discovered and validated by the registry."""

    models_with_source_kind = source_kind_models()
    labels = [model._meta.label_lower for model in models_with_source_kind]

    assert labels == sorted(labels)
    assert Template in models_with_source_kind
    assert "template" in Source.available_kinds()
    assert not [error for error in check_source_kind_contracts() if error.id.startswith("angee.integrate.")]


def test_report_status_records_integration_telemetry() -> None:
    """report_status writes telemetry on the integration row itself."""

    integration = Integration()

    integration.report_status(status=IntegrationRuntimeStatus.ERROR, error="boom")

    assert integration.lifecycle == IntegrationLifecycle.DRAFT
    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR
    assert integration.last_used_status == "error"
    assert integration.last_error == "boom"
    assert integration.last_error_at is not None
    assert integration.last_used_at is not None

    # A bare-string legacy success status maps to the healthy runtime axis.
    integration.report_status(status="active")

    assert integration.runtime_status == IntegrationRuntimeStatus.OK
    assert integration.last_used_status == "active"
    assert integration.last_error == ""
    assert integration.last_error_at is None


def test_integration_lifecycle_from_value_accepts_graphql_enum_name() -> None:
    """The integration lifecycle owner accepts GraphQL enum member names."""

    assert IntegrationLifecycle.from_value("DISABLED") is IntegrationLifecycle.DISABLED


def test_legacy_integration_status_mapping_is_deterministic() -> None:
    """Every legacy fused status maps to a lifecycle/runtime-status pair."""

    assert {
        "draft": integration_status_axes("draft"),
        "active": integration_status_axes("active"),
        "paused": integration_status_axes("paused"),
        "disabled": integration_status_axes("disabled"),
        "error": integration_status_axes("error"),
    } == {
        "draft": (IntegrationLifecycle.DRAFT, IntegrationRuntimeStatus.OK),
        "active": (IntegrationLifecycle.ACTIVE, IntegrationRuntimeStatus.OK),
        "paused": (IntegrationLifecycle.PAUSED, IntegrationRuntimeStatus.OK),
        "disabled": (IntegrationLifecycle.DISABLED, IntegrationRuntimeStatus.OK),
        "error": (IntegrationLifecycle.ACTIVE, IntegrationRuntimeStatus.ERROR),
    }


def test_report_status_updates_unsaved_integration_in_memory() -> None:
    """report_status updates an unsaved integration without trying to persist it."""

    integration = Integration()

    integration.report_status(status=IntegrationRuntimeStatus.ERROR, error="boom")

    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR
    assert integration.last_error == "boom"
