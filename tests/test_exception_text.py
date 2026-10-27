"""Exception presentation preserves Django's resolved messages and field context."""

import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from angee.errors import exception_text


@pytest.mark.parametrize("error,expected", [
    (RuntimeError("Service unavailable."), "Service unavailable."),
    (ValidationError("A note must be in review."), "A note must be in review."),
    (ValidationError(["First issue.", "Second issue."]), "First issue.; Second issue."),
    (ValidationError({"status": ["A note must be in review before publication."]}),
     "status: A note must be in review before publication."),
    (ValidationError({"name": ["Required.", "Must be unique."], "email": "Invalid address."}),
     "name: Required. Must be unique.; email: Invalid address."),
    (ValidationError({NON_FIELD_ERRORS: ["Try again."], "count": ValidationError(
        "At least %(minimum)s items are required.", params={"minimum": 2},
    )}), "Try again.; count: At least 2 items are required."),
])
def test_exception_text_uses_native_validation_messages(error, expected):
    assert exception_text(error) == expected


def test_exception_text_diagnostic_keeps_type_and_cause():
    try:
        try:
            raise LookupError("missing owner")
        except LookupError as cause:
            raise ValueError("invalid plan") from cause
    except ValueError as error:
        assert exception_text(error, diagnostic=True) == (
            "ValueError: invalid plan; caused by LookupError: missing owner"
        )


@pytest.mark.parametrize("attribute", ["__cause__", "__context__"])
def test_exception_text_diagnostic_stops_at_self_reference(attribute):
    error = RuntimeError("failed")
    setattr(error, attribute, error)

    assert exception_text(error, diagnostic=True) == "RuntimeError: failed"
    assert exception_text(error) == "failed"


def test_exception_text_diagnostic_stops_at_mixed_chain_cycle():
    first = RuntimeError("first")
    second = ValueError("second")
    first.__cause__ = second
    second.__context__ = first

    assert exception_text(first, diagnostic=True) == (
        "RuntimeError: first; caused by ValueError: second"
    )


def test_exception_text_diagnostic_respects_suppressed_context_and_explicit_cause():
    error = RuntimeError("failed")
    error.__context__ = LookupError("hidden")
    error.__suppress_context__ = True
    assert exception_text(error, diagnostic=True) == "RuntimeError: failed"

    error.__cause__ = ValidationError({"status": "Invalid state."})
    assert exception_text(error, diagnostic=True) == (
        "RuntimeError: failed; caused by ValidationError: status: Invalid state."
    )


def test_exception_text_diagnostic_formats_deep_chains_without_recursion():
    error = RuntimeError("root")
    for _ in range(1200):
        outer = RuntimeError("outer")
        outer.__cause__ = error
        error = outer

    text = exception_text(error, diagnostic=True)
    assert text.count("RuntimeError: outer") == 1200
    assert text.endswith("; caused by RuntimeError: root")
