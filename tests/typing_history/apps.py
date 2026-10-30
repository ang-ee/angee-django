"""Native app identity shared by abstract and composer-emitted fixture models."""

from django.apps import AppConfig


class TypingHistoryConfig(AppConfig):
    name = "tests.typing_history"
    label = "typing_history"

    def ready(self) -> None:
        from django.db.migrations.state import ProjectState

        self.before_typing_state = ProjectState.from_apps(self.apps)
        self.before_typing_models = {model.__name__: model for model in self.get_models()}
