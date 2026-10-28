"""Django identity for Angee's thin host composition seams."""

from typing import TYPE_CHECKING

from django.apps import AppConfig

from angee.compose.apps import BuildComposeConfig

if TYPE_CHECKING:
    from angee.compose.runtime import Runtime


class AngeeConfig(AppConfig):
    """Expose host management commands without owning foundation model policy."""

    default = True
    name = "angee"


class ProjectBuildConfig(BuildComposeConfig):
    """Keep native migration history beside each composed app's generated models."""

    default = False

    def create_runtime(self) -> Runtime:
        # Native AppConfig discovery inspects module globals before settings
        # composition completes; expose no lazy settings object during phase 1.
        from django.conf import settings

        runtime = super().create_runtime()
        modules = dict(getattr(settings, "MIGRATION_MODULES", {}))
        for label in runtime.labels:
            modules.setdefault(label, f"{runtime.runtime_module}.{label}.migrations")
        settings.MIGRATION_MODULES = modules
        return runtime
