"""Native Django app configurations for explicit build and prebuilt startup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:
    from angee.compose.runtime import Runtime


class BuildComposeConfig(AppConfig):
    """Run the single compiler in an explicitly selected native build process."""

    default = False
    name = "angee.compose"
    runtime: Runtime

    def import_models(self) -> None:
        super().import_models()
        assert self.apps is not None
        if tuple(self.apps.get_app_configs())[-1] is not self:
            raise ImproperlyConfigured(f"{self.name} must be last in INSTALLED_APPS")
        # Source AppConfig hooks have completed; concrete classes must exist
        # before native ready callbacks can inspect authentication or resources.
        self.runtime = self.create_runtime()
        self.runtime.emit_sources()
        self.runtime.import_generated_models()

    def create_runtime(self) -> Runtime:
        """Let native host AppConfig specializations supply their build policy."""

        from angee.compose.runtime import Runtime

        return Runtime.from_django()
