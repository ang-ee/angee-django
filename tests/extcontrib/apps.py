"""AppConfig for the additive zed-extension demo contributor (test-only app).

``extcontrib`` owns no definition of its own — it *contributes* a ``reviewer``
relation and a ``read`` arm to ``scopedemo/doc`` (owned by ``tests.scopedemo``)
through the adjacent ``permissions.extends.zed`` fragment. ``django-zed-rebac``
never reads that file, so this app is inert to the library; only Angee's
build-time merge (``angee.compose.permissions``) picks it up. It is the framework
regression handle proving domain vocabulary can stay in a consumer addon while
the framework owns only the seam.
"""

from __future__ import annotations

from django.apps import AppConfig


class ExtContribConfig(AppConfig):
    """Installed app contributing an additive REBAC extension to ``scopedemo/doc``."""

    name = "tests.extcontrib"
    label = "extcontrib"
    default_auto_field = "django.db.models.BigAutoField"
