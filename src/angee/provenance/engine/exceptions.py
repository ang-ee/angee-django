"""Public provenance refusals."""

from django.core.exceptions import ValidationError


class CreationKeyConflict(ValidationError):
    """A creation key was reused for different content."""


class ReplayRefused(ValidationError):
    """The retained evidence cannot safely restore this boundary."""
