"""Framework-owned management entrypoint called by every rendered ``manage.py``."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from django.core.management import execute_from_command_line

from angee.changesets import changeset
from angee.project import PROJECT_DIR_ENV, find_project_dir

SERVER_COMMANDS = frozenset({"runserver", "serve", "test"})
"""Commands whose units of work own their boundaries instead of the process.

``serve`` and ``runserver`` run the composed ASGI application: the request
middleware, the WebSocket scope middleware and Celery tasks attribute each unit of
work, and a process-wide changeset would merge every request of a one-worker
server into one row. ``test`` lets each test own its boundary. Everything else,
interactive shells included, is one logical write and keeps the command boundary.
"""


def main(argv: Sequence[str] | None = None) -> None:
    """Run one management command, attributing its captured writes to ``manage.<command>``.

    The project root is the ``manage.py`` directory when the entrypoint is a
    rendered file; ``ANGEE_PROJECT_DIR`` and ``DJANGO_SETTINGS_MODULE`` keep any
    explicit value so child processes and tests inherit the caller's settings.
    """

    arguments = list(sys.argv if argv is None else argv)
    project = find_project_dir(argv=arguments)
    if project is not None:
        os.environ.setdefault(PROJECT_DIR_ENV, str(project))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "angee.compose.settings")
    command = arguments[1] if len(arguments) > 1 else "help"
    if command in SERVER_COMMANDS:
        execute_from_command_line(arguments)
        return
    with changeset(fresh=True, activity="system", change_reason=f"manage.{command}", process=True):
        execute_from_command_line(arguments)
