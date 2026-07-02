"""Django app config for Angee's queue seam."""

from __future__ import annotations

from django.apps import AppConfig


class TasksConfig(AppConfig):
    """Host Procrastinate's Django integration for Angee queue users."""

    default = True
    name = "angee.tasks"
