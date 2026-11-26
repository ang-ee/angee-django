"""Key again each organization whose stored domain key is not its domain's under the domain rule."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Run the organization domain-key repair; a second run writes nothing."""

    help = (
        "Write again the domain key of each organization whose stored key is not its domain in the one spelling the "
        "domain rule gives: a row stored before the key existed, a domain a queryset update wrote, or a changed rule."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Party manager and report how many organizations it keyed."""

        del args, options
        keyed = apps.get_model("parties", "Party").objects.renormalize_organization_domains()
        self.stdout.write(self.style.SUCCESS(f"Saved the domain key of {keyed} organization(s) again."))
