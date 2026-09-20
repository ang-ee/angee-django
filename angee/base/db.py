"""Database selection for an operation owned by a Django write path."""

from __future__ import annotations

from typing import TypeVar

from django.db import models, router

_ModelT = TypeVar("_ModelT", bound=models.Model)


def get_write_alias(
    model: type[_ModelT],
    *,
    using: str | None = None,
    bound: models.Manager[_ModelT] | models.QuerySet[_ModelT] | None = None,
    instance: _ModelT | None = None,
) -> str:
    """Return one write alias for the operation's reads, locks and writes.

    Prefer explicit ``using``, then an explicitly bound manager/queryset, then
    a persisted instance's database, then Django's write router with the instance
    hint. An unsaved instance's database is only a router hint. Never consult
    ``bound.db``: an unbound manager/queryset can route that property as a read.
    Derive at the write owner and pass the result to every nested database API.
    """

    if using is not None:
        return using
    if bound is not None and bound._db is not None:
        return bound._db
    if instance is not None and not instance._state.adding and instance._state.db is not None:
        return instance._state.db
    return router.db_for_write(model, instance=instance)
