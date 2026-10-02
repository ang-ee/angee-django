"""Transport-independent domain refusals."""

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError


def exception_text(error: BaseException, *, diagnostic: bool = False) -> str:
    """Render resolved messages, optionally retaining exception types and causes."""
    if not isinstance(error, ValidationError):
        message = str(error)
    elif hasattr(error, "error_dict"):
        message = "; ".join(
            f"{field}: {' '.join(messages)}" if field != NON_FIELD_ERRORS else " ".join(messages)
            for field, messages in error.message_dict.items()
        )
    else:
        message = "; ".join(error.messages)
    if not diagnostic:
        return message
    detail = f"{type(error).__name__}: {message}"
    cause = error.__cause__ or (error.__context__ if not error.__suppress_context__ else None)
    return f"{detail}; caused by {exception_text(cause, diagnostic=True)}" if cause else detail


class DomainError(Exception):
    """A refusal with a stable wire code.

    Raise concrete subclasses with a nonempty code, never this base directly.
    Subclasses may carry detail for logs; detail is never projected to the wire.
    """

    code: str

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        code = getattr(cls, "code", None)
        if not isinstance(code, str) or not code.strip():
            raise TypeError("DomainError subclasses require a non-empty string code.")

    def __init__(self) -> None:
        super().__init__(self.code)


class RecordAccessSubjectRefused(DomainError, ValidationError):
    """The record's invariant refuses a holder, without exposing subject details."""

    code = "RECORD_ACCESS_SUBJECT_REFUSED"

    def __init__(self) -> None:
        ValidationError.__init__(self, self.code, code=self.code)
