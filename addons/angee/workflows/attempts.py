"""Frozen enum contracts for materialized historical workflow migrations only."""

from enum import StrEnum

from django.db import models


class AttemptCause(StrEnum):
    """Historical reason a physical attempt was allocated."""

    INITIAL = "initial"
    CONTINUATION = "continuation"
    AUTOMATIC_RETRY = "automatic_retry"
    MANUAL_RETRY = "manual_retry"
    MAP_ENGINE = "map_engine"
    TEST_FIXTURE = "test_fixture"


class AttemptResultKind(models.TextChoices, StrEnum):
    """Historical result choices preserved for serialized migration fields."""

    DONE = "done"
    WAIT = "wait"
    SUSPEND = "suspend"
    ERROR = "error"
    NO_RESULT = "no_result"
    PREPARATION_ERROR = "preparation_error"
    TRANSIENT_ERROR = "transient_error"
