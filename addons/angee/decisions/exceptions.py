"""Retryable decision transition conflicts."""

from angee.base.errors import DomainError


class RetryableDecisionError(DomainError):
    """A concurrent transition prevented admission; retry in a new transaction."""

    code = "DECISION_CONFLICT"

    def __init__(self, detail: str) -> None:
        Exception.__init__(self, detail)
