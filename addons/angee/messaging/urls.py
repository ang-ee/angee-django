"""HTTP routes contributed by the messaging addon."""

from __future__ import annotations

from django.urls import path

from angee.messaging import views

urlpatterns = [
    path("forms/<slug:slug>", views.public_webform, name="messaging_public_webform"),
]
