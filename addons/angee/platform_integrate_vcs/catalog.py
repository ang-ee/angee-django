"""Parser for a discovered addon manifest (``addon.toml``).

Reads the manifest's ``[addon]`` block — the catalog facts a marketplace row needs
— into the descriptor shape the sync upserts. Mirrors ``integrate.vcs.templates``
(the ``copier.yml`` parser): ``VcsBridge.discover`` fills ``path`` from the bearing
directory, so this owns only the manifest block.
"""

from __future__ import annotations

import tomllib
from typing import Any


def parse_addon_meta(blob: bytes) -> dict[str, Any]:
    """Return a catalog descriptor parsed from one ``addon.toml`` blob.

    Reads the same ``[addon]`` metadata the marketplace board groups by and renders —
    ``description``/``keywords``/``category`` — so a discovered (``REMOTE``) row carries
    them, not just the dependency graph.
    """

    addon = (tomllib.loads(blob.decode()) or {}).get("addon", {})
    name = str(addon.get("name", ""))
    raw_depends_on = addon.get("depends_on", ())
    return {
        "name": name,
        "label": name.rsplit(".", 1)[-1] if name else "",
        "namespace": name.split(".", 1)[0] if name else "",
        "description": str(addon.get("description", "")),
        "keywords": [str(keyword) for keyword in addon.get("keywords", ())],
        "category": str(addon.get("category", "")),
        "depends_on": [raw_depends_on] if isinstance(raw_depends_on, str) else list(raw_depends_on),
    }
