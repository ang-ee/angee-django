"""Serving system checks, registered by ServerConfig.ready()."""

from __future__ import annotations

from collections.abc import Iterator

from django.core.checks import Error
from django.urls import URLPattern, URLResolver
from django.urls.resolvers import RegexPattern, RoutePattern

type _Route = tuple[tuple[object, ...], ...]


def _routes(pattern: URLPattern | URLResolver, prefix: _Route = ()) -> Iterator[_Route]:
    native = pattern.pattern
    if type(pattern) not in (URLPattern, URLResolver) or type(native) not in (RoutePattern, RegexPattern):
        return
    # Each include matches independently. Preserve native boundaries, anchors,
    # flags and converter identity instead of approximating one combined regex.
    route = prefix + (
        (
            type(native),
            native.regex.pattern,
            native.regex.flags,
            native._is_endpoint,  # type: ignore[union-attr]  # Native attribute omitted by django-stubs.
            tuple((name, id(converter)) for name, converter in native.converters.items()),
        ),
    )
    if isinstance(pattern, URLResolver):
        for child in pattern.url_patterns:
            yield from _routes(child, route)
    else:
        yield route


def check_url_collisions(**kwargs: object) -> list[Error]:
    """Report identical native matching chains across URL contributions.

    Pattern type, regex text/flags, endpoint behavior and converter instances
    must agree at every include boundary, regardless of reverse namespace.
    Differently expressed chains and custom pattern types keep Django's ordered
    resolution without a proof. No converter is executed by this check.
    """

    del kwargs
    # The root URLconf composes only after setup; checks run after setup.
    from angee.server.urls import contributions

    owners: dict[_Route, str] = {}
    errors = []
    # Retain native resolvers throughout the proof so converter IDs stay live.
    for owner, pattern in tuple(contributions()):
        for route in _routes(pattern):
            previous = owners.get(route)
            owners[route] = owner
            if previous is not None:
                errors.append(
                    Error(
                        f"URL pattern chain {tuple(part[1] for part in route)!r} "
                        f"is claimed by {previous} and {owner}",
                        id="angee.server.E001",
                    )
                )
    return errors
