"""Django lifecycle for human decisions."""

from django.apps import AppConfig


class DecisionsConfig(AppConfig):
    """Connect evidence protection after model population."""

    default = True
    name = "angee.decisions"

    def ready(self) -> None:
        """Bind the addon's deletion receiver."""
        from angee.decisions.signals import connect

        connect()
