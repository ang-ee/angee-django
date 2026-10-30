"""Bootstrap an idempotent first platform admin user."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from angee.iam.autoconfig import bootstrap_admin_settings


class Command(BaseCommand):
    """Ensure the configured first admin exists."""

    help = "Ensure a first platform admin user exists."

    def add_arguments(self, parser: CommandParser) -> None:
        """Declare CLI overrides for the env/settings-backed bootstrap values."""

        parser.add_argument("--database", default="default")
        parser.add_argument("--username", default=None, help="Admin username. Defaults to settings/env or 'admin'.")
        parser.add_argument(
            "--email",
            default=None,
            help="Admin email. Defaults to settings/env or username@example.com.",
        )
        parser.add_argument("--password", default=None, help="Admin password. Prefer settings/env for deployment use.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Create or promote the configured admin without weakening REBAC."""

        del args
        configured = bootstrap_admin_settings()
        username = options["username"] or configured["username"] or "admin"
        email = options["email"] or configured["email"] or f"{username}@example.com"
        password = options["password"] or configured["password"]
        manager = get_user_model()._default_manager.db_manager(options["database"])
        try:
            _, created = manager.ensure_bootstrap_admin(username, email=email, password=password)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        action = "created" if created else "ensured"
        self.stdout.write(self.style.SUCCESS(f"bootstrap admin: {action} '{username}'"))
