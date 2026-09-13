"""Django config for Angee's IAM addon."""

from __future__ import annotations

from django.apps import AppConfig


class IAMConfig(AppConfig):
    """Source app manifest for Angee identity models."""

    default = True
    name = "angee.iam"
