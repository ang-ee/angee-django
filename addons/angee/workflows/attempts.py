"""Attempt constants that released workflow migrations import by path.

The workflows engine was rebuilt (``752281db0``) and no longer uses these
values. Migration history materialized before the rebuild names them in its
constraints, so this module keeps that import path loadable. Do not use it in
new code.
"""

from enum import StrEnum

from django.db import models


class AttemptCause(StrEnum):
    """Released history only: why a physical attempt existed for a step run."""

    INITIAL = "initial"
    CONTINUATION = "continuation"
    AUTOMATIC_RETRY = "automatic_retry"
    MANUAL_RETRY = "manual_retry"
    MAP_ENGINE = "map_engine"
    TEST_FIXTURE = "test_fixture"


class AttemptResultKind(models.TextChoices, StrEnum):
    """Released history only: an attempt's closed result variants."""

    DONE = "done"
    WAIT = "wait"
    SUSPEND = "suspend"
    ERROR = "error"
    NO_RESULT = "no_result"
    PREPARATION_ERROR = "preparation_error"
    TRANSIENT_ERROR = "transient_error"
