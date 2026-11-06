"""Django identity and serving checks for the serving capability."""

from functools import partial

from django.apps import AppConfig
from django.core import checks

from angee.base.hooks import check_hooks
from angee.server.checks import check_url_collisions

check_settings = partial(check_hooks, ("ANGEE_SERVER_COMMAND_CONTEXT",))


class ServerConfig(AppConfig):
    name = "angee.server"
    default = True

    def ready(self) -> None:
        """Register the URL collision and command-context checks; the root URLconf composes only after setup."""

        super().ready()
        checks.register(check_url_collisions, checks.Tags.urls)
        checks.register(check_settings, checks.Tags.models)
