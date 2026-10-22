"""Native app identity shared by abstract and composer-emitted fixture models."""

from django.apps import AppConfig


class TypingHistoryConfig(AppConfig):
    name = "tests.typing_history"
    label = "typing_history"

    def ready(self) -> None:
        from django.db.migrations.state import ProjectState

        self.before_projection = ProjectState.from_apps(self.apps)
