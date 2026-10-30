"""Messaging composes canonical model-label and diagnostic field contracts."""

import pytest
from rebac import system_context

from angee.base.fields import DiagnosticTextField, ModelLabelField
from angee.messaging.managers import _message_subtype, message_subtype_options
from angee.messaging.testing.models import MessageSubtype, ThreadNotification


@pytest.mark.django_db(transaction=True)
def test_subtype_scope_boundary_cleanup_precedes_field_canonicalization(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="messaging field owner regression"):
        subtype = _message_subtype(subtype_key="field-contract", model_label=" messaging.thre\x00ad ")
        assert subtype.model_label == "messaging.Thread"
        assert any(option["key"] == "field-contract" for option in message_subtype_options(" messaging.thre\x00ad "))
        assert isinstance(MessageSubtype._meta.get_field("model_label"), ModelLabelField)
        assert isinstance(ThreadNotification._meta.get_field("failure_reason"), DiagnosticTextField)
