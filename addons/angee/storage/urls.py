"""URL routes contributed by the storage addon."""

from __future__ import annotations

from django.urls import path

from angee.storage import views

urlpatterns = [
    path("storage/upload", views.upload, name="storage_upload"),
    path("storage/download/<path:filename>", views.download, name="storage_download"),
]
"""The proxy upload/download endpoints; GraphQL owns every other storage operation."""
