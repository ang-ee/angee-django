"""Grant each directory's owner the read of its contacts, where missing."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Run the directory owners' contact-read repair; a second run grants nothing."""

    help = (
        "Grant each directory's owner contact_reader on it, the read of its contacts that connecting a "
        "directory grants, where the owner does not hold it yet."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Directory manager and report what it granted."""

        del args, options
        granted, held = apps.get_model("parties", "Directory").objects.grant_owner_contact_reads()
        self.stdout.write(
            self.style.SUCCESS(
                f"Granted {granted} directory owner(s) the read of their directory's contacts; "
                f"{held} already held it."
            )
        )
