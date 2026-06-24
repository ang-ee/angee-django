"""Tests for the GraphQL schema artifact owner (render / emit / drift / check)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from angee.graphql.schema import GraphQLSchemas
from angee.graphql.sdl import GraphQLSdl


class _StubSchemas:
    """Minimal stand-in exposing what ``GraphQLSdl`` calls."""

    def __init__(self, rendered: dict[str, str]) -> None:
        self._rendered = rendered

    def render_sdl(self) -> dict[str, str]:
        return dict(self._rendered)

    def render_metadata(self) -> dict[str, dict[str, object]]:
        return {
            name: {"angee": {"resources": [{"modelLabel": f"tests.{name.title()}"}]}}
            for name in self._rendered
        }


def _sdl(tmp_path: Path, rendered: dict[str, str]) -> GraphQLSdl:
    """Return a ``GraphQLSdl`` over stub schemas writing under ``tmp_path``."""

    return GraphQLSdl(cast(GraphQLSchemas, _StubSchemas(rendered)), schema_dir=tmp_path / "schemas")


def test_emit_writes_each_schema(tmp_path: Path) -> None:
    """``emit`` writes SDL and metadata per rendered schema."""

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n", "console": "type Query { b: Int }\n"})
    sdl.emit()
    assert (tmp_path / "schemas" / "public.graphql").read_text(encoding="utf-8") == "type Query { a: Int }\n"
    assert (tmp_path / "schemas" / "console.graphql").read_text(encoding="utf-8") == "type Query { b: Int }\n"
    assert json.loads((tmp_path / "schemas" / "public.metadata.json").read_text(encoding="utf-8")) == {
        "angee": {"resources": [{"modelLabel": "tests.Public"}]}
    }
    assert json.loads((tmp_path / "schemas" / "console.metadata.json").read_text(encoding="utf-8")) == {
        "angee": {"resources": [{"modelLabel": "tests.Console"}]}
    }


def test_emit_prunes_removed_schema_buckets(tmp_path: Path) -> None:
    """``emit`` removes stale files from the owned schema directory."""

    schema_dir = tmp_path / "schemas"
    _sdl(tmp_path, {"public": "type Query { a: Int }\n", "console": "type Query { b: Int }\n"}).emit()

    sdl = GraphQLSdl(
        cast(GraphQLSchemas, _StubSchemas({"public": "type Query { a: Int }\n"})),
        schema_dir=schema_dir,
    )
    sdl.emit()

    assert (schema_dir / "public.graphql").exists()
    assert (schema_dir / "public.metadata.json").exists()
    assert not (schema_dir / "console.graphql").exists()
    assert not (schema_dir / "console.metadata.json").exists()
    sdl.check()


def test_emit_if_stale_writes_only_on_drift(tmp_path: Path) -> None:
    """``emit_if_stale`` writes when absent/drifted and no-ops when current."""

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n"})
    assert sdl.emit_if_stale() is True
    assert sdl.emit_if_stale() is False


def test_emit_if_stale_prunes_removed_schema_buckets(tmp_path: Path) -> None:
    """The boot hook converges after a schema bucket is removed."""

    schema_dir = tmp_path / "schemas"
    _sdl(tmp_path, {"public": "type Query { a: Int }\n", "console": "type Query { b: Int }\n"}).emit()

    sdl = GraphQLSdl(
        cast(GraphQLSchemas, _StubSchemas({"public": "type Query { a: Int }\n"})),
        schema_dir=schema_dir,
    )

    assert sdl.emit_if_stale() is True
    assert not (schema_dir / "console.graphql").exists()
    assert not (schema_dir / "console.metadata.json").exists()
    sdl.check()


def test_check_raises_on_drift_and_passes_when_current(tmp_path: Path) -> None:
    """``check`` names the stale schema, then passes once emitted."""

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n"})
    with pytest.raises(RuntimeError, match="schemas/public.graphql"):
        sdl.check()
    sdl.emit()
    sdl.check()


def test_check_raises_on_metadata_drift(tmp_path: Path) -> None:
    """``check`` names stale metadata artifacts as schema drift."""

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n"})
    sdl.emit()
    (tmp_path / "schemas" / "public.metadata.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"schemas/public\.metadata\.json"):
        sdl.check()
