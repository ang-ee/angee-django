"""Public provenance refusals."""

from django.core.exceptions import ValidationError

from angee.base.exceptions import DomainError


class CreationKeyConflict(DomainError):
    """A creation key was reused for different content."""

    code = "CREATION_KEY_CONFLICT"

    def __init__(self, *args: object) -> None:
        Exception.__init__(self, *args or (self.code,))


class ReplayRefused(ValidationError):
    """The retained evidence cannot safely restore this boundary."""
