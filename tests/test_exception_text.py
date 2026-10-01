"""Exception presentation preserves Django's resolved messages and field context."""

import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from angee.base.errors import exception_text


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
