"""Django's ``makemigrations`` with the composer's populated-column drop guard.

Only the native ``autodetector`` extension point changes; Django still owns
detection, questions, naming and writing. Installed apps override core commands
in Django's ``get_commands``, and ``angee.compose`` ships this one together with
``migrate``: Django requires both commands to share one autodetector
(``commands.E001``).
"""

from __future__ import annotations

from django.core.management.commands.makemigrations import Command as MakeMigrationsCommand

from angee.compose.migrations import DropGuardAutodetector


class Command(MakeMigrationsCommand):
    """Refuse autodetected drops of populated columns before any file is written."""

    autodetector = DropGuardAutodetector
