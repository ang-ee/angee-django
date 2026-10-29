"""GraphQL-owned deterministic SDL and metadata render, published by the compiler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings

from angee.graphql.schema import GraphQLSchemas

if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django_compose.declarations import ModelsView
    from django_compose.model_composition import ModelComposition

_SDL_SUFFIX = ".graphql"
_METADATA_SUFFIX = ".metadata.json"


class GraphQLSdl:
    """Render, write, and check generated GraphQL artifacts for each schema."""

    def __init__(self, schemas: GraphQLSchemas, *, schema_dir: Path) -> None:
        """Create a schema artifact owner over ``schemas`` writing under ``schema_dir``."""

        self.schemas = schemas
        self.schema_dir = schema_dir

    @classmethod
    def from_discovery(cls) -> GraphQLSdl:
        """Return a schema artifact owner over discovered schemas and the runtime dir."""

        return cls(
            GraphQLSchemas.from_discovery(),
            schema_dir=Path(settings.COMPOSE_OUTPUT_DIR) / "schemas",
        )

    def render(self) -> dict[str, str]:
        """Return printed SDL per schema name (the single source of truth)."""

        return self.schemas.render_sdl()

    def render_metadata(self) -> dict[str, dict[str, object]]:
        """Return JSON-safe schema metadata per schema name."""

        return self.schemas.render_metadata()

    def check(self) -> None:
        """Compare the prebuilt compiler-owned schema snapshot without writing it."""

        expected = self._rendered_artifacts()
        stale = [path for path, text in expected.items()
                 if not (self.schema_dir / path).is_file()
                 or (self.schema_dir / path).read_text(encoding='utf-8') != text]
        if stale:
            raise RuntimeError('generated GraphQL schema artifacts are stale: '
                               + ', '.join(f'schemas/{path}' for path in sorted(stale)))

    def _rendered_artifacts(self) -> dict[Path, str]:
        """Return owned schema artifact filenames mapped to rendered content."""

        artifacts = {Path(f"{name}{_SDL_SUFFIX}"): sdl for name, sdl in self.render().items()}
        artifacts.update({
            Path(f"{name}{_METADATA_SUFFIX}"): _metadata_json(metadata)
            for name, metadata in self.render_metadata().items()
        })
        return artifacts



def _metadata_json(metadata: dict[str, object]) -> str:
    """Return deterministic JSON for one schema metadata artifact."""

    return json.dumps(metadata, indent=2, sort_keys=True, default=_json_default) + "\n"


def _json_default(value: Any) -> object:
    """Reject non-JSON metadata values with a useful owner-level error."""

    raise TypeError(f"GraphQL schema metadata is not JSON serializable: {value!r}")


class SchemaIntegration:
    """Publish schemas only after all native app ready callbacks have completed."""

    external_root = "schemas"
    phase = "ready"

    def render(self, composition: ModelComposition, models: ModelsView | Apps) -> dict[Path, bytes]:
        del composition, models
        return {Path('schemas') / path: text.encode('utf-8')
                for path, text in GraphQLSdl.from_discovery()._rendered_artifacts().items()}
