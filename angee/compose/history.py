"""Keep retained runtime migration labels in Django's app registry."""

from pathlib import Path
from types import ModuleType

from django.apps import AppConfig


def historical_labels(runtime_dir: Path) -> tuple[str, ...]:
    """Return packages with migration files, including retired model owners."""

    return tuple(sorted({
        path.parent.parent.name
        for path in runtime_dir.glob("*/migrations/[0-9]*.py")
        if path.parent.parent.name.isidentifier()
    }))


class HistoricalRuntimeConfig(AppConfig):
    """Expose migration history without importing retired models or capabilities."""

    def __init__(self, runtime_dir: Path, runtime_module: str, label: str) -> None:
        module = ModuleType(f"{runtime_module}.{label}")
        module.__path__ = [str(runtime_dir / label)]
        super().__init__(module.__name__, module)
        self.models_module = ModuleType(f"{module.__name__}.models")

    def import_models(self) -> None:
        """Bind the empty native model map; stale generated models are never loaded."""

        self.models = self.apps.all_models[self.label]
