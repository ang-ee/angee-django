"""URL routes contributed by the storage addon."""

from __future__ import annotations

from django.urls import path

from angee.storage import views

urlpatterns = [
    path("storage/upload", views.upload, name="storage_upload"),
]
"""The proxy upload endpoint; GraphQL owns every other storage operation."""
