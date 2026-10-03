"""Django's ``migrate`` sharing the composer's ``makemigrations`` autodetector.

Django requires both commands to use one autodetector (``commands.E001``).
``migrate`` uses it only for its pending-model-changes notice, which therefore
reports the same populated-column refusal as ``makemigrations``.
"""

from __future__ import annotations

from django.core.management.commands.migrate import Command as MigrateCommand

from angee.compose.migrations import DropGuardAutodetector


class Command(MigrateCommand):
    """Run Django's migrate with the shared drop-guard autodetector."""

    autodetector = DropGuardAutodetector
