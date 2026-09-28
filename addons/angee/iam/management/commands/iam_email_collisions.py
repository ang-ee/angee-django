"""List person-email collisions before the host applies the unique constraint."""

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Read-only account inventory; never merges, renames, or deletes accounts."""

    help = "List person accounts sharing a normalized nonempty email. Does not modify accounts."
    requires_system_checks: list[str] = []

    def handle(self, *args: Any, **options: Any) -> None:
        """Print each colliding account through IAM's normalization owner."""

        del args, options
        count = 0
        for email, users in get_user_model().objects.person_email_collisions().items():
            for user in users:
                self.stdout.write(
                    f"{email}\tid={user.pk}\tusername={user.username}"
                    f"\tactive={user.is_active}\tstaff={user.is_staff}"
                )
                count += 1
        self.stdout.write(f"{count} person accounts with email collisions.")
