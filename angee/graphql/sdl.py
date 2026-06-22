"""On-disk GraphQL schema artifact emission, checks, and drift detection.

:class:`GraphQLSdl` is the schema counterpart of :class:`angee.compose.runtime.Runtime`:
it owns where generated schema artifacts live (``runtime/schemas/<name>.graphql`` and
``runtime/schemas/<name>.metadata.json``), renders them from the discovered
:class:`~angee.graphql.schema.GraphQLSchemas`, and reconciles disk against that render.
The ``schema`` management command and the dev-serve boot hook (:mod:`angee.asgi`) both
delegate here, so the write/check/drift logic lives once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings

from angee.fs import write_atomic
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

        rendered = self._rendered_artifacts()
        self._prune_orphans(rendered)
        for filename, content in rendered.items():
            write_atomic(self.schema_dir / filename, content)

    def emit_if_stale(self) -> bool:
        """Reconcile drifted schema artifacts and orphans; return whether any changed.

        Mirrors :meth:`angee.compose.runtime.Runtime.emit_if_stale`: drift-gated,
        idempotent, and converges the owned directory to the render.
        """

        drift = self._drift()
        if not drift:
            return False
        rendered = self._rendered_artifacts()
        self._prune_orphans(rendered)
        for filename in drift:
            if filename in rendered:
                write_atomic(self.schema_dir / filename, rendered[filename])
        return True

    def check(self) -> None:
        """Raise when on-disk schema artifacts differ from the render."""

        drift = self._drift()
        if drift:
            rendered = ", ".join(f"schemas/{filename}" for filename in drift)
            raise RuntimeError(f"generated GraphQL schema artifacts are stale: {rendered}")

    def _drift(self) -> list[str]:
        """Return schema artifact filenames whose on-disk file differs from the render."""

        expected = self._rendered_artifacts()
        actual = (
            {path.name: path.read_text(encoding="utf-8") for path in self._artifact_paths()}
            if self.schema_dir.exists()
            else {}
        )
        return sorted(
            (set(expected) ^ set(actual))
            | {name for name in expected.keys() & actual.keys() if expected[name] != actual[name]}
        )

    def _rendered_artifacts(self) -> dict[str, str]:
        """Return owned schema artifact filenames mapped to rendered content."""

        artifacts = {
            f"{name}{_SDL_SUFFIX}": sdl
            for name, sdl in self.render().items()
        }
        artifacts.update(
            {
                f"{name}{_METADATA_SUFFIX}": _metadata_json(metadata)
                for name, metadata in self.render_metadata().items()
            }
        )
        return artifacts

    def _artifact_paths(self) -> list[Path]:
        """Return all generated schema artifact paths in deterministic order."""

        if not self.schema_dir.exists():
            return []
        return sorted(
            [
                path
                for path in self.schema_dir.iterdir()
                if path.is_file()
                and (
                    path.name.endswith(_SDL_SUFFIX)
                    or path.name.endswith(_METADATA_SUFFIX)
                )
            ]
        )

    def _prune_orphans(self, rendered: dict[str, str]) -> None:
        """Remove schema artifacts for schema buckets that no longer render."""

        if not self.schema_dir.exists():
            return
        expected_names = set(rendered)
        for path in self._artifact_paths():
            if path.name not in expected_names:
                path.unlink()


def _metadata_json(metadata: dict[str, object]) -> str:
    """Return deterministic JSON for one schema metadata artifact."""

    return json.dumps(metadata, indent=2, sort_keys=True, default=_json_default) + "\n"


def _json_default(value: Any) -> object:
    """Reject non-JSON metadata values with a useful owner-level error."""

    raise TypeError(f"GraphQL schema metadata is not JSON serializable: {value!r}")
