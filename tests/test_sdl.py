"""Tests for the GraphQL schema artifact owner (render / compiler integration / drift / check)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from django.apps import apps
from django_compose.model_composition import ModelComposition

from angee.graphql import asgi as graphql_asgi
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


def test_render_returns_each_schema_without_writing(tmp_path: Path) -> None:
    """The GraphQL owner renders content; the native compiler publishes it."""

    rendered = {"public": "type Query { a: Int }\n", "console": "type Query { b: Int }\n"}
    sdl = _sdl(tmp_path, rendered)
    assert sdl.render() == rendered
    assert sdl.render_metadata() == {
        name: {"angee": {"resources": [{"modelLabel": f"tests.{name.title()}"}]}}
        for name in rendered
    }
    assert not (tmp_path / "schemas").exists()


def test_schema_integration_renders_native_compiler_artifacts(tmp_path: Path, monkeypatch) -> None:
    """The compiler integration returns deterministic SDL and metadata bytes."""

    from angee.graphql.sdl import SchemaIntegration

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n"})
    monkeypatch.setattr(GraphQLSdl, "from_discovery", classmethod(lambda cls: sdl))
    integration = SchemaIntegration()
    composition = ModelComposition({}, {})
    artifacts = integration.render(composition, apps)
    assert artifacts == {
        Path("schemas/public.graphql"): b"type Query { a: Int }\n",
        Path("schemas/public.metadata.json"): (
            json.dumps(sdl.render_metadata()["public"], indent=2, sort_keys=True) + "\n"
        ).encode(),
    }
    assert integration.render(composition, apps) == artifacts
    assert not (tmp_path / "schemas").exists()


def _compiler_snapshot(sdl: GraphQLSdl) -> None:
    """Create the prebuilt artifact snapshot consumed by the read-only checker."""

    sdl.schema_dir.mkdir(parents=True, exist_ok=True)
    for name, text in sdl.render().items():
        (sdl.schema_dir / f"{name}.graphql").write_text(text, encoding="utf-8")
    for name, metadata in sdl.render_metadata().items():
        (sdl.schema_dir / f"{name}.metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )


def test_graphql_asgi_does_not_contribute_artifact_writes() -> None:
    """Schema output belongs to explicit provisioning, including DEBUG hosts."""

    assert not hasattr(graphql_asgi, "boot_hooks")
    assert not hasattr(graphql_asgi, "GraphQLSdl")


def test_check_raises_on_drift_and_passes_when_current(tmp_path: Path) -> None:
    """``check`` names the stale schema, then passes once emitted."""

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n"})
    with pytest.raises(RuntimeError, match="schemas/public.graphql"):
        sdl.check()
    _compiler_snapshot(sdl)
    sdl.check()


def test_check_raises_on_metadata_drift(tmp_path: Path) -> None:
    """``check`` names stale metadata artifacts as schema drift."""

    sdl = _sdl(tmp_path, {"public": "type Query { a: Int }\n"})
    _compiler_snapshot(sdl)
    (tmp_path / "schemas" / "public.metadata.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"schemas/public\.metadata\.json"):
        sdl.check()
