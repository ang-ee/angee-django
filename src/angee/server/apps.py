"""Django identity and serving checks for the serving capability."""

from django.apps import AppConfig
from django.core import checks


class ServerConfig(AppConfig):
    name = "angee.server"
    default = True

    def ready(self) -> None:
        """Register the URL collision check; the root URLconf composes only after setup."""

        super().ready()
        from angee.server.checks import check_url_collisions

        checks.register(check_url_collisions, checks.Tags.urls)
