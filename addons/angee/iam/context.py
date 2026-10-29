"""IAM authorization for parsed native management commands after Django setup."""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from django.db import DEFAULT_DB_ALIAS
from rebac import database_context, system_context


@contextmanager
def command_context(command: str, argv: Sequence[str], options: Mapping[str, Any]) -> Iterator[None]:
    """Audit stock auth commands on the database selected by Django's parser."""

    del argv
    if command not in {"createsuperuser", "changepassword"}:
        yield
        return
    with database_context(options.get("database") or DEFAULT_DB_ALIAS), system_context(reason=f"auth.{command}"):
        yield
