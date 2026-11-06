"""Provider-defined organizational scope, independent of user ownership."""

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_compose.models import Composable


class NullableScopeMixin(Composable):
    """Opt into nullable organizational scope with no implicit creation default.

    The selected provider owns the relation and its policy. Without a provider,
    participation adds no field, manager, authorization or runtime dependency.
    This scope is independent of transferable user ownership.
    """

    class Meta:
        abstract = True


class DefaultedScopeMixin(Composable):
    """Opt into nullable organizational scope with a provider-owned creation default.

    An existing unscoped row stays unscoped when edited. The provider owns the
    source of the creation default; participation itself supplies no actor or
    ambient scope and does not change authorization. Choose this contract or
    NullableScopeMixin, never both on the same row.
    """

    class Meta:
        abstract = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Reject contradictory scope semantics before an optional provider runs."""

        super().__init_subclass__(**kwargs)
        if issubclass(cls, NullableScopeMixin):
            raise ImproperlyConfigured(
                f"{cls.__module__}.{cls.__qualname__} cannot compose both "
                "NullableScopeMixin and DefaultedScopeMixin."
            )
