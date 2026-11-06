"""Command contexts around Django's native command execution."""

from __future__ import annotations

from contextlib import ExitStack
from types import MappingProxyType
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.management import ManagementUtility
from django.utils.module_loading import import_string
from django_compose import management as compose_management


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
                    parsed_options = MappingProxyType(options.copy())
                    for dotted in getattr(settings, "ANGEE_SERVER_COMMAND_CONTEXT", ()):
                        stack.enter_context(
                            import_string(dotted)(original_argv[1], original_argv, parsed_options)
                        )
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
