"""User-facing exception text shared by persistence and presentation owners."""

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
