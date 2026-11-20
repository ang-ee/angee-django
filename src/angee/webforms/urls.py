"""HTTP routes contributed by the web forms addon."""

from __future__ import annotations

from django.urls import path

from angee.webforms import views

PUBLIC_PREFIX = "forms/w"
"""Where a form's public address lives: ``<prefix>/<alias>``.

An interim prefix. ``forms/<slug>`` is messaging's public form path until that
path is removed; one path has one owner, so this stays beside it until then.
"""

urlpatterns = [
    path(f"{PUBLIC_PREFIX}/<slug:alias>", views.public_form, name="webforms_public_form"),
]
