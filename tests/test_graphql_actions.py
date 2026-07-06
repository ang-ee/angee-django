"""Tests for shared GraphQL action helpers."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

import angee.graphql.actions as actions_module
from angee.base.transitions import TransitionNotAllowed
from angee.graphql.actions import (
    ActionResult,
    action_guard,
    action_target,
    resolve_action_target,
)


def test_action_result_carries_created_record_id() -> None:
    """A create-and-return verb populates ``id``; a plain result leaves it ``None``."""

    assert ActionResult(ok=True, message="ok").id is None
    created = ActionResult(ok=True, message="Payment registered.", id="pay_abc123")
    assert created.id == "pay_abc123"


def test_action_guard_maps_baseline_domain_errors() -> None:
    """The guard maps each baseline domain error to an in-band ``ActionResult``."""

    @action_guard("Could not register the payment.")
    def register(kind: str) -> ActionResult:
        if kind == "validation":
            raise ValidationError({"amount": ["Exceeds the balance."]})
        if kind == "transition":
            raise TransitionNotAllowed("status draft to paid is not allowed.")
        if kind == "missing":
            raise Group.DoesNotExist("no such row")
        return ActionResult(ok=True, message="Payment registered.")

    ok = register("ok")
    assert ok.ok is True

    validation = register("validation")
    assert validation.ok is False
    assert validation.message == "Could not register the payment."
    assert validation.validation_errors == {"amount": ["Exceeds the balance."]}

    transition = register("transition")
    assert transition.ok is False
    assert transition.validation_errors is None

    missing = register("missing")  # ObjectDoesNotExist subclass
    assert missing.ok is False


def test_action_guard_admits_addon_local_errors_and_reraises_others() -> None:
    """A resolver adds its own error types; anything unlisted propagates as a GraphQL error."""

    class PaymentRefused(Exception):
        """A payment-provider domain refusal an addon owns."""

    @action_guard("Payment refused.", errors=(PaymentRefused,))
    def charge(kind: str) -> ActionResult:
        if kind == "refused":
            raise PaymentRefused("gateway declined")
        raise RuntimeError("boom")

    refused = charge("refused")
    assert refused.ok is False
    assert refused.message == "Payment refused."

    with pytest.raises(RuntimeError, match="boom"):
        charge("other")


def test_action_result_carries_in_band_validation_errors() -> None:
    """``ActionResult`` exposes the additive in-band ``validation_errors`` map.

    A plain success omits it (default ``None``); a domain failure may return a
    field → messages map a typed-args action form binds to its inputs.
    """

    assert ActionResult(ok=True, message="ok").validation_errors is None

    failure = ActionResult(
        ok=False,
        message="Fix the amount.",
        validation_errors={"amount": ["Amount exceeds the balance."]},
    )
    assert failure.validation_errors == {"amount": ["Amount exceeds the balance."]}


def test_action_result_from_error_maps_field_validation_errors() -> None:
    """A per-field ``ValidationError`` becomes the in-band camel-cased field map."""

    error = ValidationError({"unit_price": ["Must be positive."], "quantity": ["Required."]})
    result = ActionResult.from_error(error, "Fix the line.")

    assert result.ok is False
    assert result.message == "Fix the line."
    # Keys are camel-cased to match the GraphQL argument names the form binds to.
    assert result.validation_errors == {
        "unitPrice": ["Must be positive."],
        "quantity": ["Required."],
    }


def test_action_result_from_error_keeps_non_field_errors_at_form_level() -> None:
    """A ``NON_FIELD_ERRORS`` key is preserved so it surfaces at form level, not mangled."""

    error = ValidationError({NON_FIELD_ERRORS: ["The document is out of balance."]})
    result = ActionResult.from_error(error, "Cannot post.")

    assert result.validation_errors == {NON_FIELD_ERRORS: ["The document is out of balance."]}


def test_action_result_from_error_falls_back_to_message_only() -> None:
    """A non-field ``ValidationError`` and any other exception yield a message-only result."""

    non_field = ActionResult.from_error(ValidationError("Whole thing is wrong."), "Bad request.")
    assert non_field.ok is False
    assert non_field.message == "Bad request."
    assert non_field.validation_errors is None

    other = ActionResult.from_error(RuntimeError("boom"), "Sync failed.")
    assert other.ok is False
    assert other.message == "Sync failed."
    assert other.validation_errors is None


@pytest.mark.django_db
def test_resolve_action_target_elevates_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Action target lookup runs inside the shared elevated context."""

    group = Group.objects.create(name="operators")
    reasons: list[str | None] = []

    class Context:
        """Small context manager that records entry."""

        def __enter__(self) -> None:
            return None

        def __exit__(self, *exc: object) -> None:
            return None

    def system_context(*, reason: str | None = None) -> Context:
        """Return a recording system context."""

        reasons.append(reason)
        return Context()

    monkeypatch.setattr(actions_module, "system_context", system_context)

    target = resolve_action_target(
        Group,
        str(group.pk),
        reason="tests.action",
    )

    assert target == group
    assert reasons == ["tests.action"]


@pytest.mark.django_db
def test_resolve_action_target_raises_clear_not_found() -> None:
    """Missing action targets fail with the model name and public id."""

    with pytest.raises(ValueError, match="Group 'missing' was not found."):
        resolve_action_target(
            Group,
            "missing",
            reason="tests.action.missing",
        )


@pytest.mark.django_db
def test_resolve_action_target_applies_select_related() -> None:
    """Action callers can join related rows before elevated resolution."""

    content_type = ContentType.objects.get_for_model(Group)
    permission = Permission.objects.create(
        content_type=content_type,
        codename="can_operate",
        name="Can operate",
    )

    target = resolve_action_target(
        Permission,
        str(permission.pk),
        reason="tests.action.select_related",
        select_related=("content_type",),
    )

    assert target == permission
    assert target.content_type == content_type
    assert "content_type" in target._state.fields_cache


@pytest.mark.django_db
def test_action_target_wraps_lookup_and_body_in_system_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Action target contexts reuse the same audited reason for lookup and body."""

    group = Group.objects.create(name="operators")
    reasons: list[str | None] = []
    active_depth = 0

    class Context:
        """Small context manager that records active elevation depth."""

        def __init__(self, reason: str | None) -> None:
            self.reason = reason

        def __enter__(self) -> None:
            nonlocal active_depth
            reasons.append(self.reason)
            active_depth += 1
            return None

        def __exit__(self, *exc: object) -> None:
            nonlocal active_depth
            active_depth -= 1
            return None

    def system_context(*, reason: str | None = None) -> Context:
        """Return a recording system context."""

        return Context(reason)

    monkeypatch.setattr(actions_module, "system_context", system_context)

    with action_target(Group, str(group.pk), reason="tests.action.context") as target:
        assert target == group
        assert active_depth == 1

    assert active_depth == 0
    assert reasons == ["tests.action.context", "tests.action.context"]
