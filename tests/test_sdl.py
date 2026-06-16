"""Tests for the GraphQL SDL owner (render / emit / drift / check)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from angee.graphql.schema import GraphQLSchemas
from angee.graphql.sdl import GraphQLSdl


class _StubSchemas:
    """Minimal stand-in exposing only ``render_sdl`` (what ``GraphQLSdl`` calls)."""

    def __init__(self, rendered: dict[str, str]) -> None:
        self._rendered = rendered

    def render_sdl(self) -> dict[str, str]:
        return dict(self._rendered)


def _sdl(tmp_path: Path, rendered: dict[str, str]) -> GraphQLSdl:
    """Return a ``GraphQLSdl`` over stub schemas writing under ``tmp_path``."""

    return GraphQLSdl(cast(GraphQLSchemas, _StubSchemas(rendered)), schema_dir=tmp_path / "schemas")


def test_emit_writes_each_schema(tmp_path: Path) -> None:
    """``emit`` writes one ``<name>.graphql`` per rendered schema."""

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n", "console": "type Query { b: Int }\n"})
    sdl.emit()
    assert (tmp_path / "schemas" / "public.graphql").read_text(encoding="utf-8") == "type Query { a: Int }\n"
    assert (tmp_path / "schemas" / "console.graphql").read_text(encoding="utf-8") == "type Query { b: Int }\n"


def test_emit_if_stale_writes_only_on_drift(tmp_path: Path) -> None:
    """``emit_if_stale`` writes when absent/drifted and no-ops when current."""

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n"})
    assert sdl.emit_if_stale() is True
    assert sdl.emit_if_stale() is False


def test_check_raises_on_drift_and_passes_when_current(tmp_path: Path) -> None:
    """``check`` names the stale schema, then passes once emitted."""

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n"})
    with pytest.raises(RuntimeError, match="schemas/public.graphql"):
        sdl.check()
    sdl.emit()
    sdl.check()
