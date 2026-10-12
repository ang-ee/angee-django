"""Transactional cleanup of resources owned by a stopping workflow run."""

import logging
from collections.abc import Callable
from functools import wraps

from asgiref.sync import iscoroutinefunction
from django.db import transaction
from django.dispatch import Signal

logger = logging.getLogger(__name__)


def isolated_run_cleanup[**P, R](receiver: Callable[P, R]) -> Callable[P, R | Exception]:
    """Wrap a synchronous cleanup receiver in its own savepoint before connecting it."""
    if iscoroutinefunction(receiver):
        raise TypeError("Run cleanup receivers must be synchronous to share the terminal transaction.")

    @wraps(receiver)
    def receive(*args: P.args, **kwargs: P.kwargs) -> R | Exception:
        try:
            with transaction.atomic():
                return receiver(*args, **kwargs)
        except Exception as error:
            logger.exception("Workflow cleanup receiver %r failed.", receiver)
            return error

    return receive


run_stopping = Signal()
"""Release run-owned resources once on each transition from open to terminal.

Dispatch uses ``send_robust`` inside a savepoint owned by the terminal writer,
so no receiver can abort the terminal write. Every receiver must be decorated
with ``isolated_run_cleanup`` before connecting through Django's public Signal
API. Receivers perform synchronous, database-only, idempotent cleanup in the
terminal writer's transaction; each has its own savepoint. A failure rolls back
that receiver, is logged, and lets later receivers finish. An undecorated
receiver that leaves the transaction unusable rolls back every receiver's
cleanup, still without blocking the terminal write.
Retried runs can reach another terminal transition.
The run instance may still carry its previous status. Deferred cancellation only
enqueues work: cleanup waits until the cancellation task owns the run locks.
"""
