"""Public registration readiness within Strawberry's operation lifetime.

Provider presets belong to the composed implementation registry. This snapshot
contains configured OAuth registrations, without their secret material.
"""

from collections.abc import Iterator
from contextvars import ContextVar

from django.apps import apps
from rebac import system_context
from strawberry.extensions import SchemaExtension

_ready_slugs: ContextVar[dict[str, frozenset[str]] | None] = ContextVar("integrate.ready_oauth_slugs", default=None)


def ready_registration_slugs() -> frozenset[str]:
    """Evaluate the registration owner's readiness on its preferred public rows."""

    model = apps.get_model("integrate", "OAuthClient")
    with system_context(reason="integrate.oauth.public_readiness"):
        return frozenset(row.slug for row in model.objects.ready_registrations())


def operation_registration_slugs() -> frozenset[str]:
    """Batch owner-derived public readiness within this GraphQL operation."""

    operation = _ready_slugs.get()
    if operation is None:
        return ready_registration_slugs()
    if "ready" not in operation:
        operation["ready"] = ready_registration_slugs()
    return operation["ready"]


def oauth_client_is_ready(slug: str) -> bool:
    """Read one registration from the GraphQL operation's owner-derived snapshot."""

    return slug in operation_registration_slugs()


class OAuthRegistrationExtension(SchemaExtension):
    """Keep cached readiness local to one GraphQL operation."""

    def on_operation(self) -> Iterator[None]:
        token = _ready_slugs.set({})
        try:
            yield
        finally:
            _ready_slugs.reset(token)
