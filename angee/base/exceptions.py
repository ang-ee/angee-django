"""User-facing exception text shared by persistence and presentation owners."""

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError


def exception_text(error: BaseException) -> str:
    """Render Django's resolved validation messages, preserving named field context."""
    if not isinstance(error, ValidationError):
        return str(error)
    if hasattr(error, "error_dict"):
        return "; ".join(
            f"{field}: {' '.join(messages)}" if field != NON_FIELD_ERRORS else " ".join(messages)
            for field, messages in error.message_dict.items()
        )
    return "; ".join(error.messages)
