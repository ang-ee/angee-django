"""Root URLconf: readiness plus enabled addons' ``urls`` modules through native include()."""

from __future__ import annotations

from collections.abc import Iterator

from django.apps import apps
from django.urls import URLPattern, URLResolver, include, path

from angee.base.addons import addon_modules
from angee.server.views import readiness


def contributions() -> Iterator[tuple[str, URLPattern | URLResolver]]:
    """Yield ``(app name, pattern)`` in installed app order, after Django setup.

    The server contributes readiness itself; every other enabled addon that ships
    a ``urls`` module is included unprefixed, and its own ``app_name`` selects the
    native namespace. Ordinary Django apps never opt in by shipping ``urls.py``.
    """

    apps.check_apps_ready()
    if not apps.ready:
        raise RuntimeError("URL composition requires completed django.setup()")
    yield __package__, path("health/ready", readiness, name="angee-server-readiness")
    for config, module in addon_modules("urls"):
        if config.name != __package__:
            yield config.name, path("", include(module))


urlpatterns = [pattern for _, pattern in contributions()]
