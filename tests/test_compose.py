"""Tests for build-time runtime composition."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps

from angee.compose.runtime import AngeeRuntime


def runtime_for(tmp_path: Path) -> AngeeRuntime:
    """Return a runtime that emits the installed base addon."""

    return AngeeRuntime.from_addons(
        (apps.get_app_config("base"),),
        runtime_dir=tmp_path / "runtime",
    )


def test_runtime_renders_base_resource_sources(tmp_path: Path) -> None:
    """The runtime renders source files for the base Resource model."""

    sources = runtime_for(tmp_path).render_sources()

    assert Path("__init__.py") in sources
    assert "ANGEE GENERATED RUNTIME" in sources[Path("__init__.py")]
    assert "RUNTIME_APPS = ['base']" in sources[Path("__init__.py")]
    assert "class Resource" in sources[Path("base/models.py")]
    assert ".angee-manifest.json" not in {str(path) for path in sources}


def test_runtime_emit_and_check_detect_drift(tmp_path: Path) -> None:
    """Emit writes deterministic files and check reports later drift."""

    runtime = runtime_for(tmp_path)
    runtime.emit()
    runtime.check()

    (tmp_path / "runtime" / "base" / "models.py").write_text(
        "# stale\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="stale"):
        runtime.check()


def test_runtime_clean_requires_generated_sentinel(tmp_path: Path) -> None:
    """Clean refuses to delete a non-generated configured runtime dir."""

    runtime = runtime_for(tmp_path)
    runtime.runtime_dir.mkdir()
    (runtime.runtime_dir / "handwritten.py").write_text(
        "# keep\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="not an Angee runtime directory"):
        runtime.clean()
