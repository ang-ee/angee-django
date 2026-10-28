"""Django identity for Angee's thin host composition seams."""

from django.apps import AppConfig
from django.core import checks

from angee.hooks import check_hooks


class AngeeConfig(AppConfig):
    """Expose host management commands without owning foundation model policy."""

    default = True
    name = "angee"

    def ready(self) -> None:
        """Validate declared host hooks independently of optional model capabilities."""
        super().ready()
        checks.register(check_hooks, checks.Tags.models)
