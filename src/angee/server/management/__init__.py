"""The project launcher: django-compose's ``execute`` with a server ManagementUtility.

Enabled addons contribute command contexts through ``ANGEE_SERVER_COMMAND_CONTEXT``:
dotted factories called as ``factory(command, argv, options)`` after Django setup
and native parsing, entered in order around the winning command's ``execute``.
Serving commands first run the ``SERVING_TAG`` checks.
"""

from __future__ import annotations

from contextlib import ExitStack
from types import MappingProxyType
from typing import Any

from django.apps import apps
from django.core.management import ManagementUtility
from django_compose import management as compose_management

from angee.base.hooks import resolve_hooks
from angee.server.checks import SERVING_TAG

SERVING_COMMANDS = frozenset({"check", "runserver", "serve"})
"""Commands that run ``SERVING_TAG`` checks unless they skip checks or name their own tags."""


class _Dispatch:
    """Enter configured contexts after native parsing, around command execution."""

    def __init__(self, command: Any) -> None:
        self.command = command

    def __getattr__(self, name: str) -> Any:
        return getattr(self.command, name)

    def run_from_argv(self, argv: list[str]) -> Any:
        """Adapt native execute after parsing, retaining the winning argv dispatcher.

        Custom run_from_argv implementations must call self.execute to participate.
        Factories receive command, original argv and read-only shallow-copied options.
        The native dispatcher continues to own parsing, errors and connection cleanup.
        """
        original_execute = self.command.execute
        missing = object()
        previous = vars(self.command).get("execute", missing)
        original_argv = tuple(argv)

        def execute(*args: Any, **options: Any) -> Any:
            with ExitStack() as stack:
                # Native runserver recovery fabricates ready flags, but not ready_event.
                if apps.ready_event.is_set():
                    if original_argv[1] in SERVING_COMMANDS and not any(
                        options.get(name) for name in ("skip_checks", "tags", "deploy")
                    ):
                        self.command.check(tags=[SERVING_TAG], include_deployment_checks=True)
                    parsed_options = MappingProxyType(options.copy())
                    for factory in resolve_hooks("ANGEE_SERVER_COMMAND_CONTEXT"):
                        stack.enter_context(factory(original_argv[1], original_argv, parsed_options))
                return original_execute(*args, **options)

        self.command.execute = execute
        try:
            return self.command.run_from_argv(argv)
        finally:
            if previous is missing:
                del self.command.execute
            else:
                self.command.execute = previous


class ServerManagementUtility(ManagementUtility):
    """Use Django dispatch with Angee's post-parse command contexts."""

    def fetch_command(self, subcommand: str) -> Any:
        return _Dispatch(super().fetch_command(subcommand))


def execute(argv: list[str] | None = None) -> None:
    """Launch composition and native commands with the server utility."""
    compose_management.execute(argv, utility_class=ServerManagementUtility)
