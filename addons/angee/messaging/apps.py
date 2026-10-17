"""Django config for Angee's messaging addon."""

from __future__ import annotations

from django.apps import AppConfig


class MessagingConfig(AppConfig):
    """Source app manifest for threads, messages, and channel bridges."""

    default = True
    name = "angee.messaging"
