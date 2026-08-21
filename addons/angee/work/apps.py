"""Django configuration for the work addon."""

from __future__ import annotations

from django.apps import AppConfig


class WorkConfig(AppConfig):
    """Own operational queues, stages, and their same-row task contribution."""

    default = True
    name = "angee.work"
