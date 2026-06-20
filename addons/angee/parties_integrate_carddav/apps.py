"""Django config for the CardDAV directory backend addon."""

from __future__ import annotations

from django.apps import AppConfig


class PartiesIntegrateCarddavConfig(AppConfig):
    """Source app manifest for the CardDAV directory backend."""

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.parties_integrate_carddav"
    label = "parties_integrate_carddav"
    depends_on = ("angee.parties", "angee.integrate")
