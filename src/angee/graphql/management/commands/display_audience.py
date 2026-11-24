"""List who sees a model's display fields: every GraphQL field that can show them, and who reads its holder."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser

from angee.graphql.relations import display_audience
from angee.graphql.schema import GraphQLSchemas


class Command(BaseCommand):
    """Print :func:`angee.graphql.relations.display_audience` for the named models."""

    help = (
        "List every GraphQL field that can show a model's display fields (Meta.rebac_display_fields), its kind, "
        "and the arms of its holder's read. Name models as app_label.Model; without one, every model that "
        "declares display fields."
    )
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Accept the models to list."""

        parser.add_argument("models", nargs="*", metavar="app_label.Model")

    def handle(self, *args: Any, **options: Any) -> None:
        """Print one line per field, grouped by model."""

        del args
        try:
            targets = [apps.get_model(label) for label in options["models"]]
        except (LookupError, ValueError) as error:
            raise CommandError(str(error)) from error
        targets = targets or [
            model for model in apps.get_models() if getattr(model._meta, "rebac_display_fields", None)
        ]
        schemas = GraphQLSchemas.from_discovery()
        for model in sorted(targets, key=lambda model: model._meta.label):
            declared = ", ".join(getattr(model._meta, "rebac_display_fields", None) or ()) or "nothing"
            self.stdout.write(f"{model._meta.label} (declares {declared}):")
            for entry in display_audience(model, schemas):
                self.stdout.write(
                    f"  {entry.schema} {entry.type_name}.{entry.field} [{entry.kind}] holder {entry.holder}, "
                    f"read by {' | '.join(entry.readers) or 'nobody'}"
                )
