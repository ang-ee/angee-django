"""Messaging adopts the existing field owners without rewriting retained facts."""

from pathlib import Path

import pytest
from django.apps import apps
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState
from rebac import system_context

from angee.base.fields import DiagnosticTextField, ModelLabelField
from angee.compose.migrations import RuntimeMigrations
from angee.messaging.managers import _message_subtype, message_subtype_options
from angee.messaging.runtime_migrations import adopt_field_contracts
from angee.messaging.testing.models import MessageSubtype, ThreadNotification
from tests.tables import model_tables


def released() -> ProjectState:
    state = ProjectState()
    state.add_model(ModelState("messaging", "MessageSubtype", [
        ("id", models.AutoField(primary_key=True)),
        ("key", models.CharField(max_length=128)),
        ("model_label", models.CharField(max_length=128, blank=True, default="")),
    ], options={"db_table": "test_messaging_subtype_adoption", "constraints": [
        models.UniqueConstraint(fields=("model_label", "key"), name="test_message_subtype_adoption_identity"),
    ]}))
    state.add_model(ModelState("messaging", "RetainedMessage", [
        ("id", models.AutoField(primary_key=True)),
        ("subtype", models.ForeignKey("messaging.MessageSubtype", on_delete=models.PROTECT)),
    ], options={"db_table": "test_messaging_subtype_retained_message"}))
    state.add_model(ModelState("messaging", "ThreadNotification", [
        ("id", models.AutoField(primary_key=True)),
        ("failure_reason", models.TextField(blank=True, default="")),
        ("metadata", models.JSONField(default=dict)),
    ], options={"db_table": "test_messaging_diagnostic_adoption"}))
    return state


def test_field_adoption_is_declared_once_and_matches_current_models(tmp_path: Path) -> None:
    state = released()
    owner = RuntimeMigrations((apps.get_app_config("messaging"),), runtime_dir=tmp_path, labels=("messaging",))
    origin = ("angee.messaging:adopt_field_contracts",)
    assert len(owner.schedule(state, only=origin)) == 1
    adopted = adopt_field_contracts.Migration("adopt_field_contracts", "messaging").mutate_state(state)
    assert not adopt_field_contracts.applies(adopted)
    assert owner.schedule(adopted, only=origin) == ()
    assert not adopt_field_contracts.applies(ProjectState())
    for model, field in [(MessageSubtype, "model_label"), (ThreadNotification, "failure_reason")]:
        expected = model._meta.get_field(field)
        actual = adopted.models["messaging", model.__name__.lower()].fields[field]
        assert actual.deconstruct()[1:] == expected.deconstruct()[1:]


@pytest.mark.django_db(transaction=True)
def test_field_adoption_preserves_subtype_identity_references_and_diagnostic_text() -> None:
    state = released()
    subtype = state.apps.get_model("messaging", "MessageSubtype")
    message = state.apps.get_model("messaging", "RetainedMessage")
    notification = state.apps.get_model("messaging", "ThreadNotification")
    with model_tables((subtype, message, notification)):
        scoped = subtype.objects.create(key="custom", model_label="messaging.thread")
        global_row = subtype.objects.create(key="comment", model_label="")
        retained = message.objects.create(subtype=scoped)
        reason = "Retained Unicode diagnostic: é\n" * 20
        diagnostic = notification.objects.create(failure_reason=reason, metadata={"retained": True})
        with connection.schema_editor() as editor:
            adopted = adopt_field_contracts.Migration("adopt_field_contracts", "messaging").apply(state, editor)
        current = adopted.apps.get_model("messaging", "MessageSubtype")
        assert current.objects.get(pk=scoped.pk).model_label == "messaging.Thread"
        assert current.objects.get(pk=global_row.pk).model_label == ""
        assert list(current.objects.order_by("pk").values_list("pk", "key")) == [
            (scoped.pk, "custom"), (global_row.pk, "comment"),
        ]
        retained.refresh_from_db()
        diagnostic.refresh_from_db()
        assert retained.subtype_id == scoped.pk
        assert diagnostic.failure_reason == reason
        assert diagnostic.metadata == {"retained": True}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("labels", [["removed.Unknown"], ["messaging.thread", "messaging.Thread"]])
def test_field_adoption_refuses_unknown_or_colliding_labels_without_writes(labels: list[str]) -> None:
    state = released()
    subtype = state.apps.get_model("messaging", "MessageSubtype")
    message = state.apps.get_model("messaging", "RetainedMessage")
    notification = state.apps.get_model("messaging", "ThreadNotification")
    with model_tables((subtype, message, notification)):
        for label in labels:
            subtype.objects.create(key="retained", model_label=label)
        before = list(subtype.objects.order_by("pk").values_list("pk", "key", "model_label"))
        with pytest.raises(ValueError, match="unknown model label|colliding canonical"):
            with connection.schema_editor() as editor:
                adopt_field_contracts.Migration("adopt_field_contracts", "messaging").apply(state, editor)
        assert list(subtype.objects.order_by("pk").values_list("pk", "key", "model_label")) == before


@pytest.mark.django_db(transaction=True)
def test_field_adoption_refuses_changed_diagnostics_before_normalizing_any_row() -> None:
    state = released()
    subtype = state.apps.get_model("messaging", "MessageSubtype")
    message = state.apps.get_model("messaging", "RetainedMessage")
    notification = state.apps.get_model("messaging", "ThreadNotification")
    with model_tables((subtype, message, notification)):
        scoped = subtype.objects.create(key="retained", model_label="messaging.thread")
        reason = "é" * 8200
        diagnostic = notification.objects.create(failure_reason=reason, metadata={"retained": True})
        with pytest.raises(ValueError, match=f"Notification {diagnostic.pk}.*explicit retention decision"):
            with connection.schema_editor() as editor:
                adopt_field_contracts.Migration("adopt_field_contracts", "messaging").apply(state, editor)
        scoped.refresh_from_db()
        diagnostic.refresh_from_db()
        assert scoped.model_label == "messaging.thread"
        assert diagnostic.failure_reason == reason
        assert diagnostic.metadata == {"retained": True}


@pytest.mark.django_db(transaction=True)
def test_subtype_scope_boundary_cleanup_precedes_field_canonicalization(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="messaging field owner regression"):
        subtype = _message_subtype(subtype_key="field-contract", model_label=" messaging.thre\x00ad ")
        assert subtype.model_label == "messaging.Thread"
        assert any(option["key"] == "field-contract" for option in message_subtype_options(" messaging.thre\x00ad "))
        assert isinstance(MessageSubtype._meta.get_field("model_label"), ModelLabelField)
        assert isinstance(ThreadNotification._meta.get_field("failure_reason"), DiagnosticTextField)
