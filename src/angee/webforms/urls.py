"""HTTP routes contributed by the web forms addon."""

from __future__ import annotations

from django.urls import path

from angee.webforms import views

PUBLIC_PREFIX = "forms"
"""Where a form's public address lives: ``<prefix>/<alias>``."""

urlpatterns = [
    path(f"{PUBLIC_PREFIX}/<slug:alias>", views.public_form, name="webforms_public_form"),
]
