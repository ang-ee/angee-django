"""Project package metadata into the marketplace catalogue descriptor."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from typing import Any

from angee.platform.discovery import facts_from_toml


def parse_addon_meta(blob: bytes, *, dependencies: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Project a source blob with static addon identity; other projects contribute no row."""

    facts = facts_from_toml(tomllib.loads(blob.decode("utf-8")), dependencies=dependencies)
    if not facts:
        return {}
    return {
        **facts,
        "namespace": facts["name"].split(".", 1)[0],
        "keywords": list(facts["keywords"]),
        "depends_on": list(facts["depends_on"]),
    }
