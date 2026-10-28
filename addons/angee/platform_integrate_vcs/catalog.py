"""Project native addon declarations into the marketplace catalog descriptor.

``VcsBridge.discover`` supplies the bearing directory as ``path``. Manifest
validation belongs to hatch-angee; this projection owns only catalog presentation.
"""

from __future__ import annotations

from typing import Any

from hatch_angee import parse_manifest_content


def parse_addon_meta(blob: bytes) -> dict[str, Any]:
    """Return catalog metadata from one validated ``addon.toml`` blob."""

    manifest = parse_manifest_content(blob)
    return {
        "name": manifest.name,
        "namespace": manifest.name.split(".", 1)[0],
        "description": manifest.description,
        "keywords": list(manifest.keywords),
        "category": manifest.category or "",
        "depends_on": list(manifest.depends_on),
    }
