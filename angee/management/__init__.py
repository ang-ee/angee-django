"""Framework-owned management entrypoint called by every rendered ``manage.py``."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from contextlib import nullcontext

from django.conf import settings
from django.core.management import execute_from_command_line
from django.core.management.base import CommandError, CommandParser, handle_default_options
from django.utils.module_loading import import_string

from angee import project


def main(argv: Sequence[str] | None = None) -> None:
    """Run a management command inside the configured host context, when supplied.

    The project root is the ``manage.py`` directory when the entrypoint is a
    rendered file; ``ANGEE_PROJECT_DIR`` and ``DJANGO_SETTINGS_MODULE`` keep any
    explicit value so child processes and tests inherit the caller's settings.
    ``ANGEE_MANAGEMENT_CONTEXT`` may name a dotted context-manager factory
    accepting the command name; that capability owns its attribution policy.
    """

    arguments = list(sys.argv if argv is None else argv)
    # Django exposes the default-option handler but not ManagementUtility's
    # preliminary parser. Apply only these native options before our context
    # accesses settings; leave argv and all unknown options for Django itself.
    parser = CommandParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--settings")
    parser.add_argument("--pythonpath")
    parser.add_argument("args", nargs="*")
    try:
        options, _unknown = parser.parse_known_args(arguments[2:])
        handle_default_options(options)
    except CommandError:
        pass  # The native command parser reports malformed options later.
    project.bootstrap(argv=arguments)
    command = arguments[1] if len(arguments) > 1 else "help"
    context_path = getattr(settings, "ANGEE_MANAGEMENT_CONTEXT", "")
    context = import_string(context_path)(command) if context_path else nullcontext()
    with context:
        execute_from_command_line(arguments)
