"""Framework-owned management entrypoint called by every rendered ``manage.py``."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from contextlib import nullcontext

from django.conf import settings
from django.core.management import execute_from_command_line
from django.utils.module_loading import import_string

from angee import project

SERVER_COMMANDS = frozenset({"runserver", "serve", "test"})
"""Commands whose units of work own their boundaries instead of the process.

``serve`` and ``runserver`` run the composed ASGI application: the request
middleware, the WebSocket scope middleware and Celery tasks attribute each unit of
work, and a process-wide changeset would merge every request of a one-worker
server into one row. ``test`` lets each test own its boundary. Everything else,
interactive shells included, is one logical write and keeps the command boundary.
"""


def main(argv: Sequence[str] | None = None) -> None:
    """Run a management command inside the configured host context, when supplied.

    The project root is the ``manage.py`` directory when the entrypoint is a
    rendered file; ``ANGEE_PROJECT_DIR`` and ``DJANGO_SETTINGS_MODULE`` keep any
    explicit value so child processes and tests inherit the caller's settings.
    ``ANGEE_MANAGEMENT_CONTEXT`` may name a dotted context-manager factory
    accepting the command name; server commands keep their own work boundaries.
    """

    arguments = list(sys.argv if argv is None else argv)
    project.bootstrap(argv=arguments)
    command = arguments[1] if len(arguments) > 1 else "help"
    if command in SERVER_COMMANDS:
        execute_from_command_line(arguments)
        return
    context_path = getattr(settings, "ANGEE_MANAGEMENT_CONTEXT", "")
    context = import_string(context_path)(command) if context_path else nullcontext()
    with context:
        execute_from_command_line(arguments)
