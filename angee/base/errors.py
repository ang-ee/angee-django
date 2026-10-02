"""Transport-independent domain refusals."""

from dataclasses import dataclass

from django.core.exceptions import NON_FIELD_ERRORS, ObjectDoesNotExist, PermissionDenied, ValidationError
from rebac import MissingActorError

from angee.base.transitions import TransitionNotAllowed

INTERNAL_ERROR_MESSAGE = "An unexpected error occurred."


@dataclass(frozen=True)
class ErrorClassification:
    """Public refusal classification; internal exception values never leave it."""

    code: str
    message: str

    @property
    def expected(self) -> bool:
        return self.code != "INTERNAL"


def validation_error(error: BaseException | None) -> ValidationError | None:
    """Find an authored validation refusal in an exception chain."""

    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, ValidationError):
            return error
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return None


def classify_error(error: BaseException | None) -> ErrorClassification:
    """Share domain, authentication, permission and validation wire policy."""

    if isinstance(error, DomainError):
        return ErrorClassification(error.code, error.code)
    if isinstance(error, MissingActorError):
        return ErrorClassification("UNAUTHENTICATED", "Authentication required.")
    if isinstance(error, PermissionDenied):
        return ErrorClassification("PERMISSION_DENIED", "Permission denied.")
    validation = validation_error(error)
    if validation is not None:
        return ErrorClassification("VALIDATION", " ".join(validation.messages))
    if isinstance(error, TransitionNotAllowed | ObjectDoesNotExist):
        return ErrorClassification("BAD_USER_INPUT", "The requested operation was refused.")
    return ErrorClassification("INTERNAL", INTERNAL_ERROR_MESSAGE)


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
