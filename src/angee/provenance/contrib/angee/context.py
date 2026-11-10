"""Angee management and merge lifecycle attribution defaults."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from typing import Any

from django.db import models

from angee.provenance.context import ChangesetContext, changeset, current_changeset

SERVER_COMMANDS = frozenset({"celery", "runserver", "serve", "test"})
"""Commands whose units of work own their boundaries instead of the process.

``serve`` and ``runserver`` run the composed ASGI application: the request
middleware, the WebSocket scope middleware and Celery tasks attribute each unit of
work, and a process-wide changeset would merge every request of a one-worker
server into one row. ``celery`` lets each task own its boundary; ``test`` lets each
test own its boundary. Everything else, interactive shells included, is one
logical write and keeps the command boundary.
"""


def management_context(
    command: str, argv: Sequence[str], options: Mapping[str, Any],
) -> ChangesetContext | nullcontext[None]:
    """Attribute command writes while servers, workers and tests own their boundaries."""

    del argv, options
    if command in SERVER_COMMANDS:
        return nullcontext()
    return changeset(fresh=True, activity="system", change_reason=f"manage.{command}", process=True)


@contextmanager
def merge_context(survivor: models.Model, *, join: bool = False) -> Iterator[None]:
    """Preserve a whole fold's attribution, including an unchanged survivor."""

    context = current_changeset() if join else None
    with changeset(
        fresh=context is None,
        activity="merge" if context is None else None,
        origin=survivor if context is None else None,
    ):
        yield
        if (context := current_changeset(required=True, record=True)) is not None:
            context.touched.append(type(survivor)._meta.label_lower)
