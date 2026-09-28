"""Transport-independent domain refusals."""

from django.core.exceptions import ValidationError


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
