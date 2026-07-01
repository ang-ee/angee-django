"""Bootstrap an idempotent first platform admin user."""

from __future__ import annotations

import os
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from rebac import app_settings, system_context
from rebac.roles import grant as rebac_grant


class Command(BaseCommand):
    """Ensure the configured first admin exists and holds the platform-admin role."""

    help = "Ensure a first platform admin user exists."

    def add_arguments(self, parser: CommandParser) -> None:
        """Declare CLI overrides for the env/settings-backed bootstrap values."""

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
        username = options["username"] or _configured(
            "ANGEE_BOOTSTRAP_ADMIN_USERNAME",
            env_fallbacks=("DJANGO_SUPERUSER_USERNAME",),
            default="admin",
        )
        if not username:
            raise CommandError("bootstrap_admin requires a username.")
        email = options["email"] or _configured(
            "ANGEE_BOOTSTRAP_ADMIN_EMAIL",
            env_fallbacks=("DJANGO_SUPERUSER_EMAIL",),
            default=f"{username}@example.com",
        )
        password = options["password"] or _configured(
            "ANGEE_BOOTSTRAP_ADMIN_PASSWORD",
            env_fallbacks=("DJANGO_SUPERUSER_PASSWORD",),
        )

        User = get_user_model()
        manager = User._default_manager
        lookup_manager = _system_lookup_manager(User)
        username_field = User.USERNAME_FIELD

        with system_context(reason="iam.bootstrap_admin"), transaction.atomic():
            try:
                user = lookup_manager.get(**{username_field: username})
            except User.DoesNotExist:
                if not password:
                    raise CommandError(
                        "bootstrap_admin needs ANGEE_BOOTSTRAP_ADMIN_PASSWORD "
                        "or --password when creating a new admin."
                    )
                user = manager.create_superuser(username=username, email=email, password=password)
                created = True
            else:
                created = False
                update_fields = _promote_existing_admin(user, email=email, password=password)
                if update_fields:
                    user.save(update_fields=sorted(update_fields))

            role = app_settings.REBAC_UNIVERSAL_ADMIN_ROLE
            if role:
                rebac_grant(actor=user, role=role)

        action = "created" if created else "ensured"
        self.stdout.write(self.style.SUCCESS(f"bootstrap admin: {action} '{username}'"))


def _configured(
    setting_name: str,
    *,
    env_fallbacks: tuple[str, ...] = (),
    default: str | None = None,
) -> str | None:
    """Return a non-empty value from Django settings, env, or ``default``."""

    value = getattr(settings, setting_name, None)
    if value not in (None, ""):
        return str(value)
    for env_name in (setting_name, *env_fallbacks):
        value = os.environ.get(env_name)
        if value not in (None, ""):
            return value
    return default


def _system_lookup_manager(user_model: type[Any]) -> Any:
    """Return a queryset/manager that can see users before an admin exists."""

    manager = user_model._default_manager
    system_context = getattr(manager, "system_context", None)
    if system_context is None:
        return manager
    return system_context(reason="iam.bootstrap_admin")


def _promote_existing_admin(user: Any, *, email: str | None, password: str | None) -> set[str]:
    """Promote an existing user without resetting an already usable password."""

    update_fields: set[str] = set()
    if hasattr(user, "email") and email and not getattr(user, "email"):
        user.email = email
        update_fields.add("email")
    for field in ("is_active", "is_staff", "is_superuser"):
        if hasattr(user, field) and getattr(user, field) is not True:
            setattr(user, field, True)
            update_fields.add(field)
    if password and hasattr(user, "has_usable_password") and not user.has_usable_password():
        user.set_password(password)
        update_fields.add("password")
    return update_fields
