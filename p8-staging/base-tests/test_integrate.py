"""Tests for the integration runtime abstract bases."""

from __future__ import annotations

import pytest
from django.db import models

from angee.integrate.models import Bridge, IntegrationLifecycle, IntegrationRuntimeStatus
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

    assert integration.lifecycle == IntegrationLifecycle.DISCONNECTED
    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR
    assert integration.last_used_status == "error"
    assert integration.last_error == "boom"
    assert integration.last_error_at is not None
    assert integration.last_used_at is not None

    # A healthy report clears the error it recorded, and never moves the
    # lifecycle — a status report is not the operator.
    integration.report_status(status=IntegrationRuntimeStatus.OK)

    assert integration.lifecycle == IntegrationLifecycle.DISCONNECTED
    assert integration.runtime_status == IntegrationRuntimeStatus.OK
    assert integration.last_used_status == "ok"
    assert integration.last_error == ""
    assert integration.last_error_at is None


def test_integration_lifecycle_is_connection_focused() -> None:
    """One shared lifecycle answers whether an integration is connected."""

    assert IntegrationLifecycle.values == ["disconnected", "connected", "paused"]
    assert Integration().lifecycle == IntegrationLifecycle.DISCONNECTED
    assert IntegrationLifecycle.from_value("CONNECTED") is IntegrationLifecycle.CONNECTED


def test_report_status_normalizes_names_and_values_and_rejects_the_rest() -> None:
    """A status resolves by runtime-status member name or value — the shape GraphQL serializes.

    A status arrives as the uppercase member name (``ERROR``) as often as the
    stored value. Anything that is neither fails at the vocabulary that could
    not read it, naming that vocabulary — a *lifecycle* value included, because
    the operator declares the lifecycle and a runtime report is not the
    operator, and the legacy fused values, which the ``integrate`` runtime
    migration erases from every column rather than translating here.
    """

    integration = Integration()

    for reported in ("ERROR", "error"):
        integration.report_status(status=reported)
        assert integration.runtime_status == IntegrationRuntimeStatus.ERROR

    for reported in ("OK", "ok"):
        integration.report_status(status=reported)
        assert integration.runtime_status == IntegrationRuntimeStatus.OK

    for rejected in ("CONNECTED", "connected", "paused", "nonsense", "active", "draft", "disabled"):
        with pytest.raises(ValueError, match="Unsupported integration runtime status"):
            integration.report_status(status=rejected)


def test_report_status_updates_unsaved_integration_in_memory() -> None:
    """report_status updates an unsaved integration without trying to persist it."""

    integration = Integration()

    integration.report_status(status=IntegrationRuntimeStatus.ERROR, error="boom")

    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR
    assert integration.last_error == "boom"
