"""Django lifecycle wiring for projects."""

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    """Wire the milestone's retained-receipt deletion rule."""

    default = True
    name = "angee.projects"

    def ready(self) -> None:
        """Register model-specific deletion receivers without querying data."""

        from angee.projects import signals

        signals.connect()
