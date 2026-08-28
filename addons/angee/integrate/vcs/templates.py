"""Parser for a Copier template manifest (``copier.yml``).

Reads the ``_angee`` block a template declares — its ``kind``, ``name``, and input
names — into the descriptor shape ``Template`` rows carry. Mirrors the operator's
template discovery, which reads the same block; the kind lives in the manifest,
not the directory layout, so a single recursive walk over a source's subtree finds
every template regardless of nesting.
"""

from __future__ import annotations

from typing import Any

import yaml


def parse_template_meta(blob: bytes) -> dict[str, Any]:
    """Return a template descriptor parsed from one ``copier.yml`` blob.

    The descriptor's ``path`` is filled in by ``VcsBridge.discover`` from the
    bearing directory; this parser owns only the manifest's ``_angee`` block.
    """

    document = yaml.safe_load(blob) or {}
    meta = document.get("_angee") or {}
    inputs = meta.get("inputs")
    return {
        "kind": str(meta.get("kind", "")),
        "name": str(meta.get("name", "")),
        "inputs": sorted(inputs.keys()) if isinstance(inputs, dict) else [],
    }
