"""Django lifecycle wiring for projects."""

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    """Wire project model lifecycle receivers."""

    default = True
    name = "angee.projects"
    requires_apps = ('django.contrib.contenttypes',)

    def ready(self) -> None:
        """Register model-specific deletion receivers without querying data."""

        from angee.projects import signals

        signals.connect()
