"""Retryable decision transition conflicts."""

from django.core.exceptions import PermissionDenied

from angee.base.errors import DomainError


class RetryableDecisionError(DomainError):
    """A concurrent transition prevented admission; retry in a new transaction."""

    code = "DECISION_CONFLICT"

    def __init__(self, detail: str) -> None:
        Exception.__init__(self, detail)


class ResolverAuthorityError(PermissionDenied):
    """A retained answer's resolver no longer has permission to give that answer."""
