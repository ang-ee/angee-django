"""Key again each organization whose stored domain key is not its domain under the domain-spelling rule."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Run the organization domain-key repair; a second run writes nothing."""

    help = (
        "Write again the domain key of each organization whose stored key is not its domain in the one spelling the "
        "domain-spelling rule gives: a row stored before the key existed, a domain a queryset update wrote, or a "
        "changed rule."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Party manager and report how many organizations it keyed and how many it could not."""

        del args, options
        repair = apps.get_model("parties", "Party").objects.renormalize_organization_domains()
        self.stdout.write(self.style.SUCCESS(f"Saved the domain key of {repair.saved} organization(s) again."))
        if repair.failed:
            self.stderr.write(
                self.style.ERROR(
                    f"The domain key of {repair.failed} organization(s) could not be saved; the log has each error, "
                    "and the next provision or renormalize_organization_domains run tries them again."
                )
            )
