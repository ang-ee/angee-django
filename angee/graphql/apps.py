"""Django config for Angee's GraphQL runtime addon."""

from __future__ import annotations

from django.apps import AppConfig


class GraphqlConfig(AppConfig):
    """Source app manifest for the Angee GraphQL runtime.

    Owns schema-bucket assembly, auto-CRUD, subscriptions, and SDL. Its ``ready``
    hook teaches strawberry-django how to resolve Angee's custom model value
    fields (e.g. ``MoneyField``) under ``auto`` before any schema is built.
    """

    default = True
    name = "angee.graphql"

    def ready(self) -> None:
        """Register Angee value-field GraphQL type mappings after app population."""

        super().ready()
        # Phase-2 ready hook: strawberry-django and the base field classes are
        # both importable now, and this runs before any schema build resolves auto.
        from angee.graphql.field_types import register_field_types

        register_field_types()
