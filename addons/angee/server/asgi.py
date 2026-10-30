"""Bootstrap native Django, then apply HTTP policy to its composed ASGI app."""

import os

import django
from django.apps import apps

from angee.server.signals import connect_web_statement_timeout

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
if not apps.ready:
    django.setup(set_prefix=False)
connect_web_statement_timeout()

# The native contribution owner requires completed app population.
from django_compose.asgi import application  # noqa: E402

__all__ = ["application"]
