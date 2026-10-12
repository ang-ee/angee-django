"""Django lifecycle wiring for projects."""

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    """Wire the milestone's retained-receipt deletion rule and the record-task cutover's guard."""

    default = True
    name = "angee.projects"

    def ready(self) -> None:
        """Register model-specific deletion receivers and the cutover guard's check without querying data."""

        from django.core import checks

        from angee.projects import signals
        from angee.projects.upgrade import check_cutover_guard

        signals.connect()
        checks.register(check_cutover_guard)
