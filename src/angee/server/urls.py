"""Root URLconf: readiness plus enabled addons' ``urls`` modules through native include()."""

from __future__ import annotations

from collections.abc import Iterator

from django.urls import URLPattern, URLResolver, include, path

from angee.base.addons import addon_modules
from angee.server import READINESS_ROUTE
from angee.server.views import readiness


def contributions() -> Iterator[tuple[str, URLPattern | URLResolver]]:
    """Yield ``(app name, pattern)`` in installed app order, after Django setup.

    The server contributes readiness itself through the same unprefixed
    ``include()`` boundary as addon routes, so ``angee.server.E001`` compares them.
    Every other enabled addon that ships a ``urls`` module is included unprefixed,
    and its own ``app_name`` selects the native namespace. Ordinary Django apps
    never opt in by shipping ``urls.py``.
    """

    yield __package__, path("", include([path(READINESS_ROUTE, readiness, name="angee-server-readiness")]))
    for config, module in addon_modules("urls"):
        if config.name != __package__:
            yield config.name, path("", include(module))


urlpatterns = [pattern for _, pattern in contributions()]
