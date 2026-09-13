"""Django config for Angee's spaces addon."""

from __future__ import annotations

from django.apps import AppConfig


class SpacesConfig(AppConfig):
    """Source app manifest for shared groups and their canonical rosters."""

    default = True
    name = "angee.spaces"
