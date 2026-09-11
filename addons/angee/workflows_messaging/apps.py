"""Application wiring for message-ingested workflow delivery."""

from django.apps import AppConfig


class WorkflowsMessagingConfig(AppConfig):
    """Bind messaging's durable ingest event to native workflow triggers."""

    default = True
    name = "angee.workflows_messaging"

    def ready(self) -> None:
        super().ready()
        from angee.workflows_messaging import signals

        signals.connect()
