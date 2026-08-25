"""On-disk GraphQL schema artifact rendering and reconciliation policy.

:class:`GraphQLSdl` is the schema counterpart of :class:`angee.compose.runtime.Runtime`:
it owns where generated schema artifacts live (``runtime/schemas/<name>.graphql`` and
``runtime/schemas/<name>.metadata.json``), renders them from the discovered
:class:`~angee.graphql.schema.GraphQLSchemas`, and reconciles disk against that render.
The ``schema`` management command and the dev-serve boot hook
(:mod:`angee.graphql.asgi`) both delegate here. Shared filesystem lifecycle logic
lives in :class:`angee.fs.GeneratedTree`; this owner supplies the GraphQL render and
owned-suffix policy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from angee.fs import GeneratedTree
from django.conf import settings

from angee.graphql.schema import GraphQLSchemas

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
            schema_dir=Path(settings.ANGEE_RUNTIME_DIR) / "schemas",
        )

    def render(self) -> dict[str, str]:
        """Return printed SDL per schema name (the single source of truth)."""

        return self.schemas.render_sdl()

    def render_metadata(self) -> dict[str, dict[str, object]]:
        """Return JSON-safe schema metadata per schema name."""

        return self.schemas.render_metadata()

    def emit(self) -> None:
        """Reconcile the owned schema directory to the rendered schemas."""

        self._generated_tree().reconcile(prune=True)

    def emit_if_stale(self) -> bool:
        """Reconcile drifted schema artifacts and orphans; return whether any changed.

        Mirrors :meth:`angee.compose.runtime.Runtime.emit_if_stale`: drift-gated,
        idempotent, and converges the owned directory to the render.
        """

        return self._generated_tree().reconcile(prune=True)

    def check(self) -> None:
        """Raise when on-disk schema artifacts differ from the render."""

        drift = self._generated_tree().drift()
        if drift:
            rendered = ", ".join(f"schemas/{path}" for path in drift)
            raise RuntimeError(f"generated GraphQL schema artifacts are stale: {rendered}")

    def _rendered_artifacts(self) -> dict[Path, str]:
        """Return owned schema artifact filenames mapped to rendered content."""

        artifacts = {Path(f"{name}{_SDL_SUFFIX}"): sdl for name, sdl in self.render().items()}
        artifacts.update({
            Path(f"{name}{_METADATA_SUFFIX}"): _metadata_json(metadata)
            for name, metadata in self.render_metadata().items()
        })
        return artifacts

    def _generated_tree(self) -> GeneratedTree:
        """Return the synchronizer with GraphQL's owned-suffix prune policy."""

        return GeneratedTree(
            self.schema_dir,
            self._rendered_artifacts(),
            owns=lambda path: len(path.parts) == 1 and path.name.endswith((_SDL_SUFFIX, _METADATA_SUFFIX)),
        )


def _metadata_json(metadata: dict[str, object]) -> str:
    """Return deterministic JSON for one schema metadata artifact."""

    return json.dumps(metadata, indent=2, sort_keys=True, default=_json_default) + "\n"


def _json_default(value: Any) -> object:
    """Reject non-JSON metadata values with a useful owner-level error."""

    raise TypeError(f"GraphQL schema metadata is not JSON serializable: {value!r}")
