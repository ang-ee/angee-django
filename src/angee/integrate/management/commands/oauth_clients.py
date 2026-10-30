"""Sync settings-declared OAuth/OIDC clients into the integrate registry."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Upsert the OAuth clients declared in ``settings.ANGEE_INTEGRATE_OAUTH_CLIENTS``.

    A thin dispatcher: ``OAuthClientManager.sync_from_settings`` owns reading the
    setting, validating entries, and the idempotent upsert of the OAuth base fields.
    The OIDC refinement is owned and seeded by the ``iam_integrate_oidc`` addon. The
    host owns putting secrets into that setting (from the environment); this command
    only triggers the sync after ``migrate``.
    """

    help = "Create or update OAuth/OIDC clients declared in settings."
    requires_system_checks: list[str] = []

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the settings-driven OAuth client sync and report the count."""

        del args, options
        oauth_client_model = apps.get_model("integrate", "OAuthClient")
        synced = oauth_client_model.objects.sync_from_settings()
        self.stdout.write(self.style.SUCCESS(f"Synced {len(synced)} OAuth client(s) from settings."))
