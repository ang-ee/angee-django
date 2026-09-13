"""Django configuration for the projects addon."""

from __future__ import annotations

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    """Own the personal-complete project and task domain."""

    default = True
    name = "angee.projects"

    def ready(self) -> None:
        """Wire projects-owned container binding mirrors."""

        super().ready()
        from angee.projects import signals

        signals.connect()
