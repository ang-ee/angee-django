"""Retryable decision transition conflicts."""

from django.core.exceptions import PermissionDenied


class RetryableDecisionError(Exception):
    """A concurrent transition prevented admission; retry in a new transaction."""


class ResolverAuthorityError(PermissionDenied):
    """A retained answer's resolver no longer has permission to give that answer."""
