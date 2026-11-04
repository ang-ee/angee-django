"""Django identity and explicit HTTP routes for the serving capability."""

from django.apps import AppConfig


class ServerConfig(AppConfig):
    name = "angee.server"
    default = True
    compose_urls = "angee.server.readiness_urls"

    def ready(self) -> None:
        """Register the serving URL collision check after app population."""
        super().ready()
        from angee.server import checks  # noqa: F401
