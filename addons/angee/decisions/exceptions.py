"""Retryable decision transition conflicts."""


class RetryableDecisionError(Exception):
    """A concurrent transition prevented admission; retry in a new transaction."""
